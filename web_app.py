"""Flask front-end. Each logged-in browser session owns one TLS connection to the hotel server."""
import os
import secrets
import threading

from flask import Flask, jsonify, render_template, request, session

from hotel_net import HotelConnection, ProtocolError

SOCKET_TIMEOUT = 10

app = Flask(__name__)
# Set FLASK_SECRET_KEY to keep sessions valid across restarts; otherwise a fresh key is used.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# token -> HotelConnection
connections = {}
connections_lock = threading.Lock()

CONTENT_SECURITY_POLICY = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def require_json_for_posts():
    # Browsers cannot send application/json cross-site without a CORS preflight,
    # which this app never grants, so this doubles as CSRF protection.
    if request.method == "POST" and not request.is_json:
        return error("Requests must be JSON.", 415)
    return None


def error(message, status):
    return jsonify({"ok": False, "message": message}), status


def json_body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def drop_connection(token):
    with connections_lock:
        conn = connections.pop(token, None)
    if conn:
        conn.close()


def current_connection():
    with connections_lock:
        return connections.get(session.get("token"))


def forward(cmd, **fields):
    """Relay one command to the hotel server on this session's connection."""
    conn = current_connection()
    if conn is None:
        return error("Not logged in.", 401)
    try:
        reply = conn.request(cmd, **fields)
    except (OSError, ConnectionError, ProtocolError):
        drop_connection(session.pop("token", None))
        session.pop("username", None)
        return error("Session expired or server unreachable. Please log in again.", 401)
    return jsonify(reply)


def start_session(cmd):
    body = json_body()
    username, password = body.get("username"), body.get("password")
    try:
        conn = HotelConnection(timeout=SOCKET_TIMEOUT)
    except OSError:
        return error("Cannot reach the hotel server securely. Is hotel_server.py running?", 502)

    try:
        reply = conn.request(cmd, username=username, password=password)
    except (OSError, ConnectionError, ProtocolError):
        conn.close()
        return error("The hotel server closed the connection.", 502)

    if not reply.get("ok"):
        conn.close()
        return jsonify(reply), 401 if cmd == "login" else 400

    drop_connection(session.get("token"))
    token = secrets.token_hex(16)
    with connections_lock:
        connections[token] = conn
    session.clear()
    session["token"] = token
    return jsonify({**reply, "username": username})


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/login")
def login():
    return start_session("login")


@app.post("/api/register")
def register():
    return start_session("register")


@app.post("/api/logout")
def logout():
    drop_connection(session.pop("token", None))
    session.clear()
    return jsonify({"ok": True, "message": "Logged out."})


@app.get("/api/rooms")
def rooms():
    return forward("rooms")


@app.get("/api/mine")
def mine():
    return forward("mine")


@app.post("/api/book")
def book():
    body = json_body()
    return forward("book", rooms=body.get("rooms"), nights=body.get("nights", 1))


@app.post("/api/checkout")
def checkout():
    body = json_body()
    return forward("checkout", rooms=body.get("rooms"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, threaded=True)
