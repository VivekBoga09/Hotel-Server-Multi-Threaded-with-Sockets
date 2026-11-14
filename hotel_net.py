"""Shared wire protocol and TLS client for the hotel system.

Messages are single-line JSON objects terminated by "\\n", sent over TLS.
Line framing means TCP segmentation can never merge or split a message.
"""
import json
import os
import socket
import ssl
import threading

HOST = "127.0.0.1"
PORT = 8888
CERT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "server.crt")
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "server.key")
MAX_LINE_BYTES = 65536
CLIENT_TIMEOUT = 10


class ProtocolError(Exception):
    pass


def read_message(stream):
    """Return the next message as a dict, or None if the peer closed the connection."""
    line = stream.readline(MAX_LINE_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
        raise ProtocolError("Message too long or incomplete.")
    try:
        message = json.loads(line)
    except (ValueError, RecursionError) as exc:  # bad JSON/UTF-8, or absurdly deep nesting
        raise ProtocolError("Message is not valid JSON.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Message must be a JSON object.")
    return message


def write_message(stream, payload):
    stream.write(json.dumps(payload).encode("utf-8") + b"\n")
    stream.flush()


def client_context(cafile=CERT_PATH):
    # Certificate and hostname verification stay on: the self-signed cert is
    # trusted explicitly as the only CA.
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class HotelConnection:
    """One authenticated-capable TLS connection to the hotel server."""

    def __init__(self, host=HOST, port=PORT, cafile=CERT_PATH, timeout=CLIENT_TIMEOUT):
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            self._sock = client_context(cafile).wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        self._stream = self._sock.makefile("rwb")
        self._lock = threading.Lock()

    def request(self, cmd, **fields):
        with self._lock:
            write_message(self._stream, {"cmd": cmd, **fields})
            reply = read_message(self._stream)
        if reply is None:
            raise ConnectionError("The server closed the connection.")
        return reply

    def close(self):
        try:
            self.request("exit")
        except (OSError, ProtocolError):
            pass
        finally:
            # The peer may already be gone (idle timeout, lockout); closing must not raise.
            for closable in (self._stream, self._sock):
                try:
                    closable.close()
                except OSError:
                    pass
