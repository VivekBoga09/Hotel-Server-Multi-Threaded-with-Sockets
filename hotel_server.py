"""Multithreaded TLS hotel server. One thread per client connection."""
import logging
import socket
import ssl
import threading

from hotel_core import HotelState
from hotel_net import (CERT_PATH, HOST, KEY_PATH, PORT, ProtocolError, read_message,
                       write_message)

IDLE_TIMEOUT = 300
PRELOGIN_TIMEOUT = 30
HANDSHAKE_TIMEOUT = 10
ACCEPT_POLL_SECONDS = 0.5
MAX_CLIENTS = 100
MAX_FAILED_LOGINS = 5
MAX_REGISTRATIONS = 3

log = logging.getLogger("hotel_server")


def to_reply(result):
    return {"ok": result.ok, "message": result.message, **result.data}


def error_reply(message):
    return {"ok": False, "message": message}


class Session:
    """Per-connection protocol state: who is logged in and abuse counters."""

    def __init__(self, state, peer=None):
        self._state = state
        self._peer = peer
        self.username = None
        self._failed_logins = 0
        self._registrations = 0

    def handle(self, request):
        """Return (reply, close_connection)."""
        cmd = request.get("cmd")
        if cmd == "exit":
            return {"ok": True, "message": "Goodbye."}, True
        if cmd in ("register", "login"):
            return self._authenticate(cmd, request)
        if self.username is None:
            return error_reply("Please log in first."), False
        return self._room_command(cmd, request), False

    def _authenticate(self, cmd, request):
        username, password = request.get("username"), request.get("password")
        if cmd == "register":
            if self._registrations >= MAX_REGISTRATIONS:
                return error_reply("Too many registrations on this connection."), True
            self._registrations += 1
            result = self._state.register(username, password)
            if result.ok:
                result = self._state.authenticate(username, password)
        else:
            result = self._state.authenticate(username, password)
            if not result.ok:
                self._failed_logins += 1
                if self._failed_logins >= MAX_FAILED_LOGINS:
                    return error_reply("Too many failed attempts."), True

        if result.ok:
            self.username = username
            self._failed_logins = 0
            log.info("%s %s from %s", cmd, username, self._peer)
        else:
            log.warning("failed %s for %r from %s: %s", cmd, username, self._peer, result.message)
        return to_reply(result), False

    def _room_command(self, cmd, request):
        state, user = self._state, self.username
        if cmd == "rooms":
            return {"ok": True, "message": "Room status.", "rooms": state.room_status(user)}
        if cmd == "mine":
            return to_reply(state.my_rooms(user))
        if cmd == "book":
            return to_reply(state.book(
                user,
                room_numbers=request.get("rooms"),
                count=request.get("count"),
                room_type=request.get("type"),
                nights=request.get("nights", 1),
            ))
        if cmd == "checkout":
            return to_reply(state.checkout(user, room_numbers=request.get("rooms")))
        return error_reply("Unknown command.")


def run_session(stream, sock, state, peer):
    session = Session(state, peer)
    sock.settimeout(PRELOGIN_TIMEOUT)  # unauthenticated sockets must not hold a slot for long
    while True:
        try:
            request = read_message(stream)
        except ProtocolError as exc:
            write_message(stream, error_reply(str(exc)))
            return
        if request is None:
            return
        reply, close = session.handle(request)
        if session.username is not None:
            sock.settimeout(IDLE_TIMEOUT)
        write_message(stream, reply)
        if close:
            return


def handle_connection(raw, state, context, slots):
    peer = raw.getpeername()
    try:
        with raw:
            raw.settimeout(HANDSHAKE_TIMEOUT)
            conn = context.wrap_socket(raw, server_side=True)
            with conn, conn.makefile("rwb") as stream:
                log.info("connection from %s", peer)
                run_session(stream, conn, state, peer)
    except (OSError, ssl.SSLError) as exc:
        log.info("connection from %s ended: %s", peer, exc)
    except Exception:
        # A bug triggered by one client must not take anything else down.
        log.exception("unexpected error handling %s", peer)
    finally:
        slots.release()
        log.info("disconnected %s", peer)


def serve(listener, state, context, stop_event=None):
    stop_event = stop_event or threading.Event()
    slots = threading.BoundedSemaphore(MAX_CLIENTS)
    listener.settimeout(ACCEPT_POLL_SECONDS)
    while not stop_event.is_set():
        try:
            raw, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        if not slots.acquire(blocking=False):
            raw.close()
            continue
        threading.Thread(target=handle_connection, args=(raw, state, context, slots),
                         daemon=True).start()


def server_context(cert_path=CERT_PATH, key_path=KEY_PATH):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        context = server_context()
    except (OSError, ssl.SSLError) as exc:
        raise SystemExit(f"Cannot load TLS certificate from certs/: {exc}\n"
                         "Generate one with the openssl command in README.md.")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, PORT))
    listener.listen()
    log.info("Server is listening for TLS connections on %s:%d", HOST, PORT)
    try:
        serve(listener, HotelState(), context)
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        listener.close()


if __name__ == "__main__":
    main()
