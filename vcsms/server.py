import socket
import random
import threading

from . import keys
from . import signing
from .queue import Queue
from .server_db import Server_DB
from .logger import Logger
from .cryptography import dhke, sha256, aes256, utils
from .cryptography.exceptions import CryptographyException, DecryptionFailureException
from .improved_socket import ImprovedSocket
from .message_parser import MessageParser
from .exceptions.message_parser import MessageParseException
from .exceptions.server import IDCollisionException
from .exceptions.socket import SocketException

INCOMING_MESSAGE_TYPES = {
    "GetKey": ([int, str], [10, 'utf-8']),
    "Quit": ([], []),
    "NoSuchKeyRequest": ([int], [10]),
    "MessageMalformed": ([], []),
    "CiphertextMalformed": ([], []),
    "InvalidIV": ([], []),
    "MessageDecryptionFailure": ([], [])
}

OUTGOING_MESSAGE_TYPES = {
    "KeyFound": ([int, int, int], [10, 16, 16]),
    "KeyNotFound": ([int], [10]),
    "UnknownMessageType": ([str], ['utf-8']),
    "InvalidIV": ([], []),
    "CiphertextMalformed": ([], []),
    "MessageMalformed": ([], []),
    "MessageDecryptionFailure": ([], [])
}


class Server:
    """A VCSMS messaging server. Provides messaging capabilities to clients."""
    def __init__(self, addr: str, port: int, keypair: tuple, db_path: str, pubkey_directory: str, logger: Logger):
        """Initialise a VCSMS server.

        Args:
            addr (str): The IP address of the network interface to bind to.
            port (int): The TCP port to bind to.
            keypair (tuple[tuple[int, int], tuple[int, int]]): The public and private RSA keys for the server to use in the form (exponent, modulus).
            db_path (str): The file path at which the sqlite3 database is stored.
            pubkey_directory (str): The directory to store all client public keys under.
            logger (Logger): An instance of vcsms.logger.Logger to use for logging errors/events that occur in the server.
        """
        self._addr = addr
        self._port = port
        self._pub = keypair[0]
        self._priv = keypair[1]
        self._dhke_group = dhke.group14_2048
        self._client_outboxes = {}
        self._client_sockets = {}
        self._db_path = db_path
        self._pubkey_path = pubkey_directory
        self._logger = logger
        response_map = {
            "GetKey": self._handler_get_key,
            "Quit": self._handler_quit,
            "default": self._handler_default
        }
        self._message_parser = MessageParser(INCOMING_MESSAGE_TYPES, OUTGOING_MESSAGE_TYPES, response_map)

    def run(self):
        """Begin listening for and processing connections from clients. This should be the first method that is called by this class."""
        l_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        l_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        l_sock.bind((self._addr, self._port))
        l_sock.listen(30)
        db = self._db_connect()
        db.setup_db()
        db.close()
        self._logger.log(f"Running on {self._addr}:{self._port}", 0)
        while True:
            conn, addr = l_sock.accept()
            self._logger.log(f"New connection from: {addr}", 2)
            ns_sock = ImprovedSocket(conn)
            ns_sock.run()
            t_connect = threading.Thread(target=self._handshake, args=(ns_sock,))
            t_connect.start()

    def _send(self, client: str, message: bytes):
        """Send a message to a specified client ID.

        Args:
            client (str): The client ID to send the message to
            message (bytes): The raw message bytes to send.
        """
        if client not in self._client_outboxes:
            self._logger.log(f"Message to offline/unknown user {client}", 3)
            self._client_outboxes[client] = Queue()
        self._client_outboxes[client].push(message)

    def _handshake(self, client: ImprovedSocket):
        """Handshake with a socket to establish its client ID, setup an encrypted connection and begin routing messages to/from it.

        Args:
            client (NonStreamSocket): An instance of vcsms.non_stream_socket.NonStreamSocket which should be a wrapper around a newly connected tcp socket.
        """
        pub_exp = hex(self._pub[0])[2:].encode()
        pub_mod = hex(self._pub[1])[2:].encode()
        client.send(pub_exp + b':' + pub_mod)
        identity_packet = client.recv()
        if identity_packet == b"MalformedIdentity":
            self._logger.log("Connection failure. Client reported a malformed public key.", 1)
            client.close()
            return
        elif identity_packet == b"PubKeyFpMismatch":
            self._logger.log("Connection failure. Client reported a fingerprint mismatch.", 1)
            client.close()
            return
        try:
            c_id, c_exp, c_mod = identity_packet.split(b':')
            c_id = c_id.decode()
        except ValueError:
            self._logger.log("Connection failure. Malformed identity packet.", 1)
            client.send(b"MalformedIdentity")
            client.close()
            return
        self._logger.log(f"Client authenticating as {c_id}", 2)
        client_pubkey = (int(c_exp, 16), int(c_mod, 16))
        if keys.fingerprint(client_pubkey) != c_id:
            self._logger.log(f"Connection failure. Public key hash mismatch for {c_id}", 1)
            client.send(b"PubKeyIdMismatch")
            client.close()
            return

        dhke_priv = random.randrange(1, self._dhke_group[1])
        dhke_pub, dhke_sig = signing.gen_signed_dh(dhke_priv, self._priv, self._dhke_group)
        client.send(hex(dhke_pub)[2:].encode() + b":" + dhke_sig)
        pubkey_auth_packet = client.recv()
        if pubkey_auth_packet == b"BadSignature":
            self._logger.log(f"Connection failure. Client reported an incorrect signature.", 1)
            client.close()
            return
        elif pubkey_auth_packet == b"MalformedDiffieHellman":
            self._logger.log(f"Connection failure. Client reported a malformed DH signature authentication packet.", 1)
            client.close()
            return
        try:
            c_dhke_pub, c_dhke_pub_sig = pubkey_auth_packet.split(b':')
        except ValueError:
            self._logger.log(f"Connection failure. Malformed DH signature authentication packet.", 1)
            client.send(b"MalformedDiffieHellman")
            client.close()
            return
        if not signing.verify(c_dhke_pub, c_dhke_pub_sig, client_pubkey):
            self._logger.log(f"Connection failure. Bad signature from {c_id}", 1)
            client.send(b"BadSignature")
            client.close()
            return
        shared_key = dhke.calculate_shared_key(dhke_priv, int(c_dhke_pub, 16), self._dhke_group)
        encryption_key = sha256.hash(utils.i_to_b(shared_key))
        db = self._db_connect()
        try:
            db.user_login(c_id, client_pubkey)
        except IDCollisionException:
            self._logger.log(f"Connection Failure. Client ID {c_id} provided a key which collides with another.", 1)
            db.close()
            client.send(b"IDCollision")
            client.close()
            return
        self._logger.log(f"User {c_id} successfully authenticated", 1)
        enc_iv = random.randrange(1, 2**128)
        random_data = random.randbytes(32)
        encrypted_confirmation = aes256.encrypt_cbc(random_data, encryption_key, enc_iv)
        client.send(hex(enc_iv)[2:].encode('utf-8') + b':' + encrypted_confirmation.hex().encode('utf-8'))
        client_confirm = client.recv()
        if client_confirm == b"MalformedChallenge":
            self._logger.log("Connection Failure. Client reported a malformed confirmation packet.", 1)
            client.close()
            return
        if client_confirm == b"CouldNotDecrypt":
            self._logger.log("Connection Failure. Client was unable to decrypt confirmation challenge.", 1)
            client.close()
            return
        try:
            client_confirm = bytes.fromhex(client_confirm.decode('utf-8'))
        except ValueError:
            self._logger.log("Connection Failure. Malformed challenge response.", 1)
            client.send(b"MalformedResponse")
            client.close()
            return
        if client_confirm != random_data:
            self._logger.log("Connection Failure. Client did not confirm handshake success.", 1)
            client.send(b"Incorrect")
            client.close()
            return
        client.send(b"OK")
        if c_id in self._client_outboxes:
            outbox = self._client_outboxes[c_id]
        else:
            outbox = Queue()
            self._client_outboxes[c_id] = outbox

        self._client_sockets[c_id] = client
        t_in = threading.Thread(target=self._in_thread, args=(client, encryption_key, c_id))
        t_out = threading.Thread(target=self._out_thread, args=(client, outbox, encryption_key))
        t_in.start()
        t_out.start()
        db.close()

    # thread methods
    def _in_thread(self, client: ImprovedSocket, encryption_key: int, client_id: str):
        """A function to be run by a thread which parses, handles if necessary, and routes incoming messages from a given client.

        Args:
            client (NonStreamSocket): The client socket to listen to.
            encryption_key (int): The encryption key to use for all messages exchanged with the client.
            client_id (str): The client ID associated with this socket.
        """
        while client.connected:
            if client.new:
                try:
                    raw = client.recv()
                    try:
                        aes_iv, ciphertext = raw.decode().split(':', 1)
                    except ValueError:
                        self._logger.log(f"Malformed message from {client_id}", 2)
                        error_msg = self._message_parser.construct_message("0", "CiphertextMalformed")
                        self._send(client_id, error_msg)
                        continue
                    try:
                        aes_iv = int(aes_iv, 16)
                    except ValueError:
                        self._logger.log(f"Invalid initialization vector {aes_iv}", 2)
                        error_msg = self._message_parser.construct_message("0", "InvalidIV")
                        self._send(client_id, error_msg)
                        continue
                    try:
                        data = aes256.decrypt_cbc(bytes.fromhex(ciphertext), encryption_key, aes_iv)
                    except CryptographyException:
                        self._logger.log(f"Could not decrypt message from {client_id}", 2)
                        error_msg = self._message_parser.construct_message("0", "MessageDecryptionFailure")
                        self._send(client_id, error_msg)
                        continue
                    try:
                        recipient, message_type, message_values = self._message_parser.parse_message(data)
                    except MessageParseException as parse_exception:
                        self._logger.log(str(parse_exception), 2)
                        error_msg = self._message_parser.construct_message("0", "MessageMalformed") 
                        self._send(client_id, error_msg)
                        continue

                    if recipient == "0":
                        response = self._message_parser.handle(client_id, message_type, message_values, "0")
                        if response:
                            self._send(client_id, response)
                    else:
                        self._logger.log(f"{message_type} {client_id} -> {recipient}", 4)
                        to_send = self._message_parser.construct_message(client_id, message_type, *message_values)
                        self._send(recipient, to_send)
                except SocketException:
                    self._logger.log(f"Failed to receive data from {client_id}, socket disconnected", 2)
                    continue
        
        client.close()
        db = self._db_connect()
        db.user_logout(client_id)
        db.close()
        self._logger.log(f"User {client_id} closed the connection", 1)
        self._client_sockets.pop(client_id)

    def _out_thread(self, client: ImprovedSocket, outbox: Queue, encryption_key: int):
        """A function to be run by a thread which constantly reads messages from
        the outbox queue, encrypts them, and sends them to the given client socket.

        Args:
            sock (NonStreamSocket): The socket for the client with whom the outbox
                is associated.
            outbox (Queue): A queue of messages meant for a specific client.
            encryption_key (int): The encryption key to for all messages exchanged
                with the client.
        """
        while client.connected:
            if not outbox.empty:
                message = outbox.pop()
                aes_iv = random.randrange(1, 2 ** 128)
                ciphertext = aes256.encrypt_cbc(message, encryption_key, aes_iv).hex()
                try:
                    client.send(hex(aes_iv).encode() + b':' + ciphertext.encode('utf-8'))
                except SocketException:
                    self._logger.log("Failed to send data to client, socket disconnected", 2)
                    continue

    # message type handler methods
    def _handler_get_key(self, sender: str, request_index: int, target_id: str) -> tuple[str, tuple]:
        """Handler function for the GetKey message type.

        Args:
            sender (str): The client ID which sent the message.
            request_index (int): The index of the request on the client side.
            target_id (str): The client ID being requested.

        Returns:
            tuple[str, tuple]: KeyFound if successful.
                KeyNotFound: The public key for the requested user could not be found.
        """
        self._logger.log(f"User {sender} requested key for user {target_id}", 3)
        db = self._db_connect()
        if db.user_known(target_id):
            self._logger.log(f"Key found for user {target_id}", 3)
            key = db.get_pubkey(target_id)
            db.close()
            return "KeyFound", (request_index, *key)

        self._logger.log(f"Key not found for user {target_id}", 3)
        db.close()
        return "KeyNotFound", (request_index, )

    def _handler_quit(self, sender: str):
        """Handler function for the Quit message type.

        Args:
            sender (str): The client ID which sent the message.
        """
        self._logger.log(f"User {sender} requested a logout", 1)
        self._client_sockets[sender].close()

    def _handler_default(self, sender: str, message_type: str, values: list):
        """Default handler for messages.
        Args:
            sender (str): The client ID which sent the message.
            message_type (str): The message type they sent.
            values (list): The parameters included in the message.
        """
        self._logger.log(f"{sender} sent message of type {message_type}. No action taken.", 3)

    def _handler_unknown(self, sender: str, message_type: str, values: list):
        """Handler for messages of unknown type.

        Args:
            sender (str): The client ID which sent the message.
            message_type (str): The message type they sent.
            values (list): The parameters included in the message.
        """
        self._logger.log(f"{sender} sent message of unknown type {message_type}", 2)
        return "UnknownMessageType", (message_type, )

    def _db_connect(self) -> Server_DB:
        """Get a connection to the server database.

        Returns:
            Server_DB: An server database connection object
        """
        db = Server_DB(self._db_path, self._pubkey_path)
        return db
