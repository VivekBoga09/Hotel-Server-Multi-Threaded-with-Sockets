"""Command-line client for the hotel server (TLS)."""
import getpass

from hotel_net import HotelConnection, ProtocolError

MENU = (
    "\nCommands:\n"
    "  rooms     show all rooms\n"
    "  book      book rooms (pick numbers, or a count and type)\n"
    "  mine      show my booked rooms and bill\n"
    "  checkout  check out some or all of my rooms\n"
    "  exit      quit\n"
)


def prompt_int(label, default=None):
    raw = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
    if not raw and default is not None:
        return default
    try:
        return int(raw)
    except ValueError:
        return None


def parse_numbers(raw):
    try:
        return [int(part) for part in raw.replace(",", " ").split()]
    except ValueError:
        return None


def show(reply):
    print(("OK: " if reply.get("ok") else "Error: ") + reply.get("message", ""))


def show_rooms(rooms):
    print(f"{'Room':<6}{'Type':<10}{'$/night':<9}Status")
    for room in rooms:
        status = "yours" if room["mine"] else "taken" if room["is_occupied"] else "free"
        print(f"{room['room_number']:<6}{room['type']:<10}{room['price']:<9}{status}")


def do_book(conn):
    raw = input("Room numbers (e.g. 1 2 5), or blank to choose by count: ").strip()
    nights = prompt_int("Nights", default=1)
    if raw:
        numbers = parse_numbers(raw)
        if numbers is None:
            print("Error: room numbers must be whole numbers.")
            return
        show(conn.request("book", rooms=numbers, nights=nights))
        return
    count = prompt_int("How many rooms")
    room_type = input("Type (standard/deluxe/suite, blank = any): ").strip().lower() or None
    show(conn.request("book", count=count, type=room_type, nights=nights))


def do_checkout(conn):
    raw = input("Room numbers to check out, or blank for all: ").strip()
    numbers = parse_numbers(raw) if raw else None
    if raw and numbers is None:
        print("Error: room numbers must be whole numbers.")
        return
    show(conn.request("checkout", rooms=numbers))


def authenticate(conn):
    while True:
        choice = input("(l)ogin or (r)egister? ").strip().lower()
        if choice not in ("l", "r"):
            continue
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        reply = conn.request("login" if choice == "l" else "register",
                             username=username, password=password)
        show(reply)
        if reply.get("ok"):
            return


def main():
    try:
        conn = HotelConnection()
    except OSError as exc:
        raise SystemExit(f"Cannot connect securely to the hotel server: {exc}")

    try:
        authenticate(conn)
        print(MENU)
        while True:
            action = input("> ").strip().lower()
            if action == "exit":
                break
            elif action == "rooms":
                show_rooms(conn.request("rooms")["rooms"])
            elif action == "book":
                do_book(conn)
            elif action == "mine":
                show(conn.request("mine"))
            elif action == "checkout":
                do_checkout(conn)
            else:
                print(MENU)
    except (ConnectionError, ProtocolError, OSError) as exc:
        print(f"Connection lost: {exc}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
