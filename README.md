# Hotel Server (Multi-Threaded with Sockets & TLS)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Security](https://img.shields.io/badge/Security-TLS%201.2%2B%20%7C%20scrypt-green.svg)](https://tools.ietf.org/html/rfc7914)
[![Protocol](https://img.shields.io/badge/Protocol-JSON--over--TLS-orange.svg)]()
[![Architecture](https://img.shields.io/badge/Architecture-Multithreaded%20Socket%20Server-purple.svg)]()

A high-performance, multithreaded client-server hotel reservation and management system built using Python socket programming. All client-server communications are encrypted with **TLS 1.2+**, using a custom newline-delimited JSON wire protocol. The project features both an interactive **CLI client** and a modern **Flask web interface**.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture & Design](#architecture--design)
- [Project Structure](#project-structure)
- [Room Inventory & Rates](#room-inventory--rates)
- [Wire Protocol Specification](#wire-protocol-specification)
- [Installation & Setup](#installation--setup)
- [Usage Guide](#usage-guide)
  - [1. Starting the Multithreaded Server](#1-starting-the-multithreaded-server)
  - [2. Using the Command-Line Client (CLI)](#2-using-the-command-line-client-cli)
  - [3. Running the Web Application](#3-running-the-web-application)
- [Running Tests](#running-tests)
- [Security Features](#security-features)
- [Documentation & Resources](#documentation--resources)

---

## Overview

In traditional client-server networking, managing concurrent access to shared resources (like hotel room bookings) requires careful synchronization, robust connection lifecycle management, and strict channel security. 

This project demonstrates:
1. **Low-level Socket Programming:** Using Python's native `socket` library to manage TCP connections.
2. **Multithreaded Concurrency:** Spawning dedicated worker threads per connection to handle simultaneous clients without blocking.
3. **Transport Layer Security (TLS):** Wrapping raw sockets with `ssl.SSLContext` ensuring in-transit privacy and integrity.
4. **Thread-Safe State Synchronization:** Mutex locks (`threading.Lock`) guarding in-memory state against race conditions and double-bookings.
5. **Decoupled Architecture:** Separation of pure business domain logic, wire protocol framing, socket server loop, and client presentations.

---

## Key Features

- **Multithreaded Connection Handling:** Serves up to 100 concurrent clients simultaneously with connection pools, socket timeouts, and graceful disconnection.
- **TLS 1.2+ Encryption:** Enforces minimum TLS 1.2 with custom certificate validation for end-to-end socket privacy.
- **Custom JSON Framing:** Newline-delimited (`\n`) JSON messaging with buffer limits (`MAX_LINE_BYTES = 64 KB`) to prevent TCP fragmentation / concatenation issues.
- **Advanced Authentication:**
  - Password hashing via `hashlib.scrypt` (memory-hard, resistant to GPU/ASIC brute forcing).
  - Timing attack defense via dummy salt computation for non-existent users.
  - Constant-time comparison using `hmac.compare_digest`.
  - Account lockout protection: 5 failed attempts locks the account for 60 seconds across all connections.
- **Atomic Booking Transactions:** All-or-nothing reservations across multiple room types and nights.
- **Dual Client Interfaces:**
  - Full-featured **Interactive CLI** with masked password inputs.
  - Modern, responsive **Web Dashboard** with real-time status and RESTful endpoints.

---

## Architecture & Design

```
+-------------------------------------------------------------+
|                        Clients                              |
|   +-----------------------+     +-----------------------+   |
|   |   CLI Client          |     |   Web UI (Browser)    |   |
|   |  (hotel_client.py)    |     |   (HTML5, CSS, JS)    |   |
|   +-----------+-----------+     +-----------+-----------+   |
+---------------|-----------------------------|---------------+
                |                             | (HTTP / REST)
                |                             v
                |                 +-----------------------+
                |                 | Flask Proxy Backend   |
                |                 | (web_app.py)          |
                |                 +-----------+-----------+
                |                             |
                +--------------+--------------+
                               | TLS 1.2+ / TCP Socket (Port 8888)
                               v
+-------------------------------------------------------------+
|                      Server Engine                          |
|   +-----------------------------------------------------+   |
|   | Multithreaded TLS Listener (hotel_server.py)        |   |
|   | - Connection pool & thread dispatch                 |   |
|   | - Session state & abuse prevention                  |   |
|   +--------------------------+--------------------------+   |
|                              |                              |
|   +--------------------------v--------------------------+   |
|   | Wire Protocol & Framing (hotel_net.py)              |   |
|   | - JSON line framing (read_message, write_message)   |   |
|   +--------------------------+--------------------------+   |
|                              |                              |
|   +--------------------------v--------------------------+   |
|   | Core Domain & State Model (hotel_core.py)           |   |
|   | - Thread-safe state (threading.Lock)                |   |
|   | - scrypt password hashing & lockout management      |   |
|   | - Inventory, booking transactions & checkout billing|   |
|   +-----------------------------------------------------+   |
+-------------------------------------------------------------+
```

---

## Project Structure

```text
.
├── certs/
│   ├── server.crt             # Self-signed X.509 TLS certificate
│   └── server.key             # RSA private key (2048-bit)
├── static/
│   ├── app.css                # Web dashboard styles
│   └── app.js                 # Frontend interactive logic
├── templates/
│   └── index.html             # Web dashboard UI template
├── tests/
│   ├── test_hotel_core.py     # Unit tests for domain logic & security
│   ├── test_integration.py    # Integration tests over live TLS sockets
│   └── test_web.py            # Unit tests for Flask web proxy
├── .gitignore                 # Git ignore patterns
├── cneh.pdf                   # System design & project overview documentation
├── hotel_client.py            # Interactive command-line client
├── hotel_core.py              # Pure domain logic (thread-safe hotel state)
├── hotel_net.py               # Shared protocol, line framing & TLS client
├── hotel_server.py            # Multithreaded TLS server daemon
├── Hotel_Server_Project_Guide.pdf # Detailed project specifications guide
├── README.md                  # Comprehensive documentation
├── requirements.txt           # Project dependencies
└── web_app.py                 # Flask web application bridging to socket server
```

---

## Room Inventory & Rates

The hotel initializes with 10 rooms across 3 categories:

| Room Numbers | Room Category | Capacity | Price per Night |
|:------------:|:-------------:|:--------:|:---------------:|
| **1 – 4**    | Standard      | 1 – 2    | **$50**         |
| **5 – 8**    | Deluxe        | 2 – 3    | **$90**         |
| **9 – 10**   | Suite         | 4 – 5    | **$150**        |

- **Duration:** 1 to 30 nights.
- **Booking Rules:** A booking is atomic (all-or-nothing). Users can book by exact room numbers or by specifying type and quantity.
- **Checkout & Billing:** Checking out releases the room(s) and produces an itemized summary calculation (`nights * rate`).

---

## Wire Protocol Specification

The protocol uses **Newline-Delimited JSON** over TLS 1.2+. Each message is a single-line JSON string ending with `\n`.

### Client Request Schema
```json
{
  "cmd": "<command_name>",
  "<param1>": "<value1>"
}
```

### Supported Commands

| Command | Arguments | Description |
|---|---|---|
| `register` | `username`, `password` | Create a new user account (min 6 chars, scrypt hashed). |
| `login` | `username`, `password` | Authenticate the current socket session. |
| `rooms` | *none* | View status of all rooms (available vs booked by user). |
| `mine` | *none* | Retrieve all rooms booked by current logged-in user. |
| `book` | `rooms`: `[1, 2]`, `nights`: `3` | Book by specific room numbers. |
| `book` | `type`: `"deluxe"`, `count`: `2`, `nights`: `2` | Book by room category and count. |
| `checkout` | `rooms`: `[1]` (or omitted for all) | Check out room(s) and compute final bill. |
| `exit` | *none* | Gracefully terminate connection. |

### Server Response Schema
```json
{
  "ok": true,
  "message": "Operation status description",
  "rooms": [],
  "total": 300
}
```

---

## Installation & Setup

### 1. Prerequisites
- **Python 3.10+**
- `pip` package manager
- (Optional) `openssl` CLI (if generating fresh certificates)

### 2. Clone the Repository
```bash
git clone https://github.com/VivekBoga09/Hotel-Server-Multi-Threaded-with-Sockets.git
cd Hotel-Server-Multi-Threaded-with-Sockets
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. (Optional) Generate Fresh TLS Certificates
Pre-generated localhost certificates are included in `certs/` for convenience. To generate new self-signed certificates:

**Linux / macOS:**
```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

**Windows (PowerShell / Git Bash):**
```bash
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

---

## Usage Guide

### 1. Starting the Multithreaded Server
Start the server in a terminal window:
```bash
python hotel_server.py
```
*The server will bind to `127.0.0.1:8888` and wait for TLS connections.*

```text
INFO:hotel_server:Server listening on 127.0.0.1:8888
```

### 2. Using the Command-Line Client (CLI)
Open one or more additional terminals and run:
```bash
python hotel_client.py
```

Follow the interactive prompts:
```text
Connecting to 127.0.0.1:8888 over TLS...
Connected.
1. Log in
2. Register
Choose: 2
Username: alice
Password: 
Account created for alice.
```

Available menu options:
- `1. View all rooms`
- `2. View my booked rooms`
- `3. Book rooms by room number`
- `4. Book rooms by room type`
- `5. Check out`
- `6. Exit`

### 3. Running the Web Application
Start the Flask web bridge:
```bash
python web_app.py
```
Open your browser and navigate to:
```
http://127.0.0.1:5000
```
The web dashboard provides:
- User registration & login with session persistence.
- Visual room status cards (Available, Booked by You, Occupied).
- Instant booking modal with price calculation.
- Check-out drawer with itemized receipt display.

---

## Running Tests

The test suite covers domain logic, concurrency, security, network protocol framing, live TLS connections, and Flask endpoints.

To run all tests using Python's built-in `unittest`:

```bash
python -m unittest discover -s tests
```

Or run individual test modules:
```bash
# Domain model & security tests (scrypt, lockout, thread-safety)
python -m unittest tests/test_hotel_core.py

# Network integration tests over live TLS sockets
python -m unittest tests/test_integration.py

# Flask web interface unit tests
python -m unittest tests/test_web.py
```

---

## Security Features

| Security Mechanism | Implementation | Threat Mitigated |
|---|---|---|
| **TLS 1.2+** | `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` with minimum version TLS 1.2 | Eavesdropping, man-in-the-middle (MITM) attacks |
| **scrypt Password Hashing** | $N=2^{14}$, $r=8$, $p=1$, 16-byte random salt | Rainbow table lookups, GPU-accelerated hash cracking |
| **Dummy Salt Computation** | Computes identical scrypt cost when username does not exist | User enumeration via response timing discrepancies |
| **Constant-time Digest Comparison** | `hmac.compare_digest` | Side-channel timing attacks |
| **Account Lockout Policy** | 5 consecutive bad passwords triggers 60-second cooldown | Online brute force and dictionary attacks |
| **Pre-login Connection Timeout** | 30-second socket timeout prior to authentication | Slowloris and connection starvation attacks |
| **Message Buffer Caps** | 64 KB line length limit with `\n` delimiter validation | Buffer exhaustion and memory denial of service |
| **Web Security Headers** | CSP, X-Content-Type-Options, X-Frame-Options, JSON CSRF defense | XSS, clickjacking, and MIME-sniffing |

---

## Documentation & Resources

- `Hotel_Server_Project_Guide.pdf`: Detailed architecture requirements, socket state diagrams, and protocol definitions.
- `cneh.pdf`: Supplementary coursework documentation and networking design rationale.

---

## License

This project is licensed under the MIT License - feel free to use and adapt for educational and practical applications.
