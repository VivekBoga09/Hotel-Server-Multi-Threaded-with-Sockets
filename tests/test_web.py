import functools
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_app  # noqa: E402
from hotel_core import HotelState  # noqa: E402
from hotel_net import HotelConnection  # noqa: E402
from hotel_server import serve, server_context  # noqa: E402


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=serve, args=(self.listener, HotelState(), server_context(), self.stop), daemon=True
        )
        self.thread.start()
        self._original = web_app.HotelConnection
        web_app.HotelConnection = functools.partial(HotelConnection, port=port)

    def tearDown(self):
        for token in list(web_app.connections):
            web_app.drop_connection(token)
        web_app.HotelConnection = self._original
        self.stop.set()
        self.thread.join(timeout=3)
        self.listener.close()

    def client(self):
        return web_app.app.test_client()

    def post(self, client, path, body=None):
        response = client.post(path, json=body if body is not None else {})
        return response.status_code, response.get_json()

    def signed_in(self, name):
        client = self.client()
        status, data = self.post(client, "/api/register", {"username": name, "password": "secret1"})
        self.assertEqual(status, 200, data)
        return client


class WebTests(WebTestCase):
    def test_page_and_static_assets_served_with_security_headers(self):
        client = self.client()
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("default-src 'self'", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        self.assertEqual(client.get("/static/app.js").status_code, 200)
        self.assertEqual(client.get("/static/app.css").status_code, 200)

    def test_api_requires_login(self):
        client = self.client()
        self.assertEqual(client.get("/api/rooms").status_code, 401)
        self.assertEqual(self.post(client, "/api/book", {"rooms": [1]})[0], 401)

    def test_non_json_post_rejected(self):
        response = self.client().post("/api/login", data="username=a&password=b",
                                      content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 415)

    def test_non_object_json_body_is_a_client_error_not_a_crash(self):
        client = self.signed_in("alice")
        for path in ("/api/book", "/api/checkout", "/api/login"):
            self.assertLess(client.post(path, json=[1, 2]).status_code, 500)

    def test_register_login_book_checkout_flow(self):
        client = self.signed_in("alice")
        rooms = client.get("/api/rooms").get_json()["rooms"]
        self.assertEqual(len(rooms), 10)
        status, booked = self.post(client, "/api/book", {"rooms": [1, 9], "nights": 2})
        self.assertEqual((status, booked["total"]), (200, 400))
        self.assertEqual(client.get("/api/mine").get_json()["total"], 400)
        status, out = self.post(client, "/api/checkout", {"rooms": [1]})
        self.assertEqual(out["total"], 100)
        self.assertEqual(self.post(client, "/api/checkout", {"rooms": None})[1]["total"], 300)

    def test_bad_login_and_duplicate_registration(self):
        self.signed_in("alice")
        other = self.client()
        self.assertEqual(self.post(other, "/api/login", {"username": "alice", "password": "wrong-pass"})[0], 401)
        self.assertEqual(self.post(other, "/api/register", {"username": "alice", "password": "secret1"})[0], 400)
        self.assertEqual(other.get("/api/rooms").status_code, 401)

    def test_two_browsers_cannot_double_book(self):
        a, b = self.signed_in("alice"), self.signed_in("bobby")
        self.assertEqual(self.post(a, "/api/book", {"rooms": [3]})[0], 200)
        status, data = self.post(b, "/api/book", {"rooms": [3]})
        self.assertFalse(data["ok"])
        rooms = b.get("/api/rooms").get_json()["rooms"]
        self.assertTrue(rooms[2]["is_occupied"] and not rooms[2]["mine"])

    def test_logout_ends_session(self):
        client = self.signed_in("alice")
        self.post(client, "/api/logout")
        self.assertEqual(client.get("/api/rooms").status_code, 401)

    def test_server_down_gives_clear_error(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.listener.close()
        status, data = self.post(self.client(), "/api/login", {"username": "alice", "password": "secret1"})
        self.assertEqual(status, 502)
        self.assertIn("hotel_server.py", data["message"])


if __name__ == "__main__":
    unittest.main()
