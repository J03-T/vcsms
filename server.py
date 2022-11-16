import socket
import random
from queue import Queue
from server_db import Server_DB
import json
import threading
import keys
import signing

from cryptographylib import dhke, sha256, aes256, utils

port = 6000
interface = "0.0.0.0"


class Server:
    def __init__(self, addr: str, port: int, keypair: tuple, db_path: str, pubkey_directory: str):
        self.addr = addr
        self.port = port
        self.pub = keypair[0]
        self.priv = keypair[1]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.dhke_group = dhke.group14_2048
        self.in_queue = Queue()
        self.out_queue = Queue()
        self.client_outboxes = {}
        self.db_path = db_path
        self.pubkey_path = pubkey_directory


    def handshake(self, client: socket.socket):
        pub_exp = hex(self.pub[0])[2:].encode()
        pub_mod = hex(self.pub[1])[2:].encode()
        client.send(pub_exp + b':' + pub_mod)
        auth_packet = client.recv(2048)
        c_id, c_exp, c_mod = auth_packet.split(b':')
        print(f"Client ID is {c_id.decode()}")
        client_pubkey = (int(c_exp, 16), int(c_mod, 16))
        if keys.fingerprint(client_pubkey) != int(c_id, 16):
            print(f"Public Key Validation Failed")
            client.send(b"PUBLIC KEY VALIDATION FAILED")
            client.close()
            return

        db = self.db_connect()
        db.user_login(c_id.decode(), client_pubkey)
        db.close()
        
        dhke_priv = random.randrange(1, self.dhke_group[1])
        dhke_pub = hex(dhke.generate_public_key(dhke_priv, self.dhke_group))[2:].encode()
        dhke_pub_sig = signing.sign(dhke_pub, self.priv)
        client.send(dhke_pub + b":" + dhke_pub_sig)

        c_dhke_pub, c_dhke_pub_sig = client.recv(2048).split(b':')

        if not signing.verify(c_dhke_pub, c_dhke_pub_sig, client_pubkey):
            client.send(b"SIGNATURE VERIFICATION FAILED")
            client.close()
            return

        shared_key = dhke.calculate_shared_key(dhke_priv, int(c_dhke_pub, 16), self.dhke_group)

        encryption_key = sha256.hash(utils.i_to_b(shared_key))
        outbox = Queue()
        self.client_outboxes[c_id.decode()] = outbox
        t_in = threading.Thread(target=self.__in_thread, args=(client, encryption_key, c_id.decode()), daemon=True)
        t_out = threading.Thread(target=self.__out_thread, args=(client, outbox, encryption_key), daemon=True)
        t_in.start()
        t_out.start()


    def __in_thread(self, client: socket.socket, encryption_key: int, id: str):
        while True:
            dat = client.recv(4096)
            iv, data = dat.split(b':', 1)
            iv = int(iv, 16)
            data = aes256.decrypt_cbc(utils.i_to_b(int(data, 16)), encryption_key, iv)
            recipient, msg = data.split(b':', 1)
            if recipient.decode() not in self.client_outboxes:
                self.client_outboxes[recipient.decode()] = Queue()
            if recipient == b'0':
                request = msg.split(b':')
                if request[0] == b'GetKey':
                    req_id = request[1].decode()
                    print(f"key request for {req_id}")
                    db = self.db_connect()
                    if db.user_known(req_id):
                        print("Found")
                        key = db.get_pubkey(req_id)
                        self.client_outboxes[id].put(b'0:KeyFound:' + req_id.encode() + b':' + hex(key[0]).encode() + b':' + hex(key[1]).encode())
                        db.close()
                        continue
                    else:
                        print("Not Found")
                        self.client_outboxes[id].put(b'0:KeyNotFound:' + request[1])
                        continue
                elif request[0] == b'QUIT':
                    db = self.db_connect()
                    db.user_logout(id)
                    db.close()
                    self.client_outboxes[id].put(b'CLOSE')
                    break
            outgoing_msg = id.encode() + b':' + msg
            self.client_outboxes[recipient.decode()].put(outgoing_msg)
            print(f"Message to {recipient} from {id}")

    def __out_thread(self, sock: socket.socket, outbox: Queue, encryption_key: int):
        while True:
            message = outbox.get()
            if message == b'CLOSE':
                sock.send(b'CLOSE')
                print("closing")
                break
            aes_iv = random.randrange(1, 2**128)
            encrypted_message = hex(int.from_bytes(aes256.encrypt_cbc(message, encryption_key, aes_iv), 'big')).encode()
            sock.send(hex(aes_iv).encode() + b':' + encrypted_message)
        sock.close()
    def connect(self, client: socket.socket):
        self.handshake(client)

    def send(self, client: str, message: bytes):
        self.client_outboxes[client].put(message)

    def run(self):
        self.sock.bind((self.addr, self.port))
        self.sock.listen(30)
        db = self.db_connect()
        db.setup_db()
        db.close()
        
        while True:
            conn, addr = self.sock.accept()
            print(f"New connection from: {addr}")
            t_connect = threading.Thread(target=self.connect, args=(conn,), daemon=True)
            t_connect.start()

    def db_connect(self):
        db = Server_DB(self.db_path, self.pubkey_path)
        return db
    
if __name__ == "__main__":
    try:
        keypair = keys.load_key("server.pub"), keys.load_key("server.priv")
    except FileNotFoundError:
        keypair = keys.generate_keys("server.pub", "server.priv")

    with open("server.conf", 'w') as f:
        f.write(json.dumps({
            "ip": "127.0.0.1",
            "port": 6000,
            "fingerprint": hex(keys.fingerprint(keypair[0]))[2:]
        }))
    server = Server("0.0.0.0", 6000, keypair, "server.sqlite", ".")
    server.run()
