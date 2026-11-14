"""Hotel domain logic: accounts, rooms, booking and checkout.

No networking lives here, so everything is unit-testable. All state changes
happen under a single lock; callers receive immutable Result objects.
"""
import hashlib
import hmac
import os
import re
import threading
import time
from dataclasses import dataclass, field

ROOM_LAYOUT = (("standard", 4, 50), ("deluxe", 4, 90), ("suite", 2, 150))  # (type, count, price/night)
MAX_NIGHTS = 30

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
MAX_TRACKED_FAILURES = 10000
LOCKOUT_MESSAGE = "Too many failed attempts. Try again in a minute."

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
HASH_BYTES = 32


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    data: dict = field(default_factory=dict)


def _fail(message):
    return Result(False, message)


def _hash_password(password, salt):
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=HASH_BYTES
    )


class HotelState:
    def __init__(self, layout=ROOM_LAYOUT, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._users = {}
        # username -> {"count": failed attempts, "locked_until": clock time}; kept for
        # unknown names too, so lockouts cannot be used to probe which accounts exist.
        self._failures = {}
        self._rooms = []
        for room_type, count, price in layout:
            for _ in range(count):
                self._rooms.append(
                    {"number": len(self._rooms) + 1, "type": room_type, "price": price,
                     "owner": None, "nights": 0}
                )
        # Used to burn equal time when the username is unknown, so response
        # timing doesn't reveal which usernames exist.
        self._dummy_salt = os.urandom(SALT_BYTES)

    # ---- accounts -------------------------------------------------------

    def register(self, username, password):
        if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
            return _fail("Username must be 3-20 letters, digits or underscores.")
        if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            return _fail(f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters.")

        salt = os.urandom(SALT_BYTES)
        digest = _hash_password(password, salt)
        with self._lock:
            if username in self._users:
                return _fail("That username is already taken.")
            self._users[username] = {"salt": salt, "hash": digest}
        return Result(True, f"Account created for {username}.")

    def authenticate(self, username, password):
        generic_failure = _fail("Invalid username or password.")
        if not isinstance(username, str) or not isinstance(password, str):
            return generic_failure
        if len(password) > MAX_PASSWORD_LENGTH:
            return generic_failure

        with self._lock:
            user = self._users.get(username)
            if self._is_locked_out(username):
                return _fail(LOCKOUT_MESSAGE)
        salt = user["salt"] if user else self._dummy_salt
        candidate = _hash_password(password, salt)
        valid = user is not None and hmac.compare_digest(candidate, user["hash"])
        with self._lock:
            if not valid:
                self._record_failure(username)
                return generic_failure
            self._failures.pop(username, None)
        return Result(True, f"Welcome, {username}.")

    def _is_locked_out(self, username):
        entry = self._failures.get(username)
        return entry is not None and entry["locked_until"] > self._clock()

    def _record_failure(self, username):
        now = self._clock()
        if len(self._failures) >= MAX_TRACKED_FAILURES:
            self._failures = {k: v for k, v in self._failures.items() if v["locked_until"] > now}
            if len(self._failures) >= MAX_TRACKED_FAILURES:
                return
        entry = self._failures.get(username, {"count": 0, "locked_until": 0})
        count = 1 if entry["locked_until"] and entry["locked_until"] <= now else entry["count"] + 1
        locked_until = now + LOCKOUT_SECONDS if count >= MAX_FAILED_ATTEMPTS else entry["locked_until"]
        self._failures[username] = {"count": count, "locked_until": locked_until}

    # ---- rooms ----------------------------------------------------------

    def room_status(self, username):
        with self._lock:
            return [
                {"room_number": r["number"], "type": r["type"], "price": r["price"],
                 "is_occupied": r["owner"] is not None, "mine": r["owner"] == username}
                for r in self._rooms
            ]

    def my_rooms(self, username):
        with self._lock:
            held = [self._booking_view(r) for r in self._rooms if r["owner"] == username]
        total = sum(r["subtotal"] for r in held)
        if not held:
            return Result(True, "You have no booked rooms.", {"rooms": [], "total": 0})
        numbers = ", ".join(str(r["room_number"]) for r in held)
        return Result(True, f"Your rooms: {numbers}. Total ${total}.", {"rooms": held, "total": total})

    def book(self, username, room_numbers=None, count=None, room_type=None, nights=1):
        if not isinstance(nights, int) or isinstance(nights, bool) or not 1 <= nights <= MAX_NIGHTS:
            return _fail(f"Nights must be a whole number from 1 to {MAX_NIGHTS}.")
        if room_type is not None and (
            not isinstance(room_type, str) or room_type not in {t for t, _, _ in ROOM_LAYOUT}
        ):
            return _fail("Unknown room type.")

        with self._lock:
            chosen = self._select_rooms(room_numbers, count, room_type)
            if isinstance(chosen, Result):
                return chosen
            for room in chosen:
                room["owner"] = username
                room["nights"] = nights
            booked = [self._booking_view(r) for r in chosen]

        total = sum(r["subtotal"] for r in booked)
        numbers = ", ".join(str(r["room_number"]) for r in booked)
        return Result(True, f"Booked room(s) {numbers} for {nights} night(s). Total ${total}.",
                      {"rooms": booked, "total": total})

    def checkout(self, username, room_numbers=None):
        with self._lock:
            mine = [r for r in self._rooms if r["owner"] == username]
            if room_numbers is None:
                released = mine
            else:
                wanted = self._clean_numbers(room_numbers)
                if wanted is None:
                    return _fail("Room numbers must be a list of whole numbers.")
                owned = {r["number"] for r in mine}
                not_owned = [n for n in wanted if n not in owned]
                if not_owned:
                    return _fail(f"You do not hold room(s) {', '.join(map(str, not_owned))}.")
                released = [r for r in mine if r["number"] in wanted]

            if not released:
                return _fail("You have no rooms to check out.")
            bill = [self._booking_view(r) for r in released]
            for room in released:
                room["owner"] = None
                room["nights"] = 0

        total = sum(r["subtotal"] for r in bill)
        numbers = ", ".join(str(r["room_number"]) for r in bill)
        return Result(True, f"Checked out room(s) {numbers}. Amount due ${total}.",
                      {"rooms": bill, "total": total})

    # ---- helpers (call with the lock held) --------------------------------

    @staticmethod
    def _booking_view(room):
        return {"room_number": room["number"], "type": room["type"], "price": room["price"],
                "nights": room["nights"], "subtotal": room["price"] * room["nights"]}

    @staticmethod
    def _clean_numbers(values):
        if not isinstance(values, list) or not values:
            return None
        if any(not isinstance(v, int) or isinstance(v, bool) for v in values):
            return None
        return sorted(set(values))

    def _select_rooms(self, room_numbers, count, room_type):
        if room_numbers is not None:
            wanted = self._clean_numbers(room_numbers)
            if wanted is None:
                return _fail("Room numbers must be a list of whole numbers.")
            by_number = {r["number"]: r for r in self._rooms}
            missing = [n for n in wanted if n not in by_number]
            if missing:
                return _fail(f"No such room(s): {', '.join(map(str, missing))}.")
            taken = [n for n in wanted if by_number[n]["owner"] is not None]
            if taken:
                return _fail(f"Room(s) already taken: {', '.join(map(str, taken))}.")
            return [by_number[n] for n in wanted]

        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            return _fail("Please specify a valid number of rooms.")
        free = [r for r in self._rooms
                if r["owner"] is None and (room_type is None or r["type"] == room_type)]
        if len(free) < count:
            return _fail(f"Only {len(free)} matching room(s) available.")
        return free[:count]
