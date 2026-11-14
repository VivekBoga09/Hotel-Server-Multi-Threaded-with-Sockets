import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotel_core import HotelState  # noqa: E402
from hotel_net import HotelConnection  # noqa: E402
from hotel_server import MAX_FAILED_LOGINS, serve, server_context  # noqa: E402


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=serve, args=(self.listener, HotelState(), server_context(), self.stop), daemon=True
        )
        self.thread.start()
        self.connections = []

    def tearDown(self):
        for conn in self.connections:
            conn.close()
        self.stop.set()
        self.thread.join(timeout=3)
        self.listener.close()

    def connect(self):
        conn = HotelConnection(port=self.port)
        self.connections.append(conn)
        return conn

    def register(self, name):
        conn = self.connect()
        reply = conn.request("register", username=name, password="secret1")
        self.assertTrue(reply["ok"], reply)
        return conn


class TlsTests(ServerTestCase):
    def test_connection_is_encrypted_with_modern_tls(self):
        conn = self.connect()
        self.assertIn(conn._sock.version(), ("TLSv1.2", "TLSv1.3"))

    def test_plaintext_client_cannot_talk_to_server(self):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        raw.sendall(b'{"cmd": "login", "username": "a", "password": "b"}\n')
        try:
            data = raw.recv(1024)
        except OSError:
            data = b""
        raw.close()
        self.assertNotIn(b'"ok"', data)


class ProtocolTests(ServerTestCase):
    def test_commands_require_login(self):
        reply = self.connect().request("book", count=1)
        self.assertFalse(reply["ok"])
        self.assertIn("log in", reply["message"])

    def test_register_book_query_checkout_flow(self):
        conn = self.register("alice")
        booked = conn.request("book", rooms=[1, 9], nights=2)
        self.assertTrue(booked["ok"], booked)
        self.assertEqual(booked["total"], 400)
        self.assertEqual(conn.request("mine")["total"], 400)
        rooms = conn.request("rooms")["rooms"]
        self.assertTrue(rooms[0]["mine"] and rooms[8]["mine"])
        out = conn.request("checkout", rooms=[1])
        self.assertEqual(out["total"], 100)
        self.assertEqual(conn.request("checkout")["total"], 300)

    def test_login_from_second_connection_sees_same_bookings(self):
        self.register("alice").request("book", rooms=[2])
        again = self.connect()
        self.assertTrue(again.request("login", username="alice", password="secret1")["ok"])
        self.assertEqual(len(again.request("mine")["rooms"]), 1)

    def test_two_clients_cannot_book_the_same_room(self):
        a, b = self.register("alice"), self.register("bobby")
        self.assertTrue(a.request("book", rooms=[5])["ok"])
        self.assertFalse(b.request("book", rooms=[5])["ok"])

    def test_wrong_password_rejected_and_connection_closed_after_limit(self):
        self.register("alice")
        conn = self.connect()
        for _ in range(MAX_FAILED_LOGINS):
            reply = conn.request("login", username="alice", password="wrong-pass")
            self.assertFalse(reply["ok"])
        with self.assertRaises((ConnectionError, OSError)):
            conn.request("login", username="alice", password="secret1")

    def test_malformed_and_unknown_requests_do_not_crash_server(self):
        conn = self.register("alice")
        self.assertFalse(conn.request("nonsense")["ok"])
        self.assertFalse(conn.request("book", rooms="1")["ok"])
        self.assertFalse(conn.request("book", count="many")["ok"])
        self.assertTrue(conn.request("mine")["ok"])

    def test_lockout_survives_reconnecting(self):
        self.register("alice")
        for _ in range(MAX_FAILED_LOGINS):
            self.connect().request("login", username="alice", password="wrong-pass")
        reply = self.connect().request("login", username="alice", password="secret1")
        self.assertFalse(reply["ok"])
        self.assertIn("Too many", reply["message"])

    def test_deeply_nested_json_is_rejected_without_killing_the_connection_thread(self):
        conn = self.connect()
        conn._stream.write(b"[" * 30000 + b"]" * 30000 + b"\n")
        conn._stream.flush()
        self.assertIn(b'"ok": false', conn._stream.readline())

    def test_type_confusion_in_book_request_is_handled(self):
        conn = self.register("alice")
        self.assertFalse(conn.request("book", count=1, type=["suite"])["ok"])
        self.assertTrue(conn.request("mine")["ok"])

    def test_oversized_message_is_rejected(self):
        conn = self.connect()
        conn._stream.write(b"x" * 70000 + b"\n")
        conn._stream.flush()
        reply = conn._stream.readline()
        self.assertIn(b'"ok": false', reply)


if __name__ == "__main__":
    unittest.main()
