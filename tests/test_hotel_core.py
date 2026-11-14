import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hotel_core import HotelState  # noqa: E402


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.state = HotelState()

    def test_register_then_authenticate(self):
        self.assertTrue(self.state.register("alice", "secret1").ok)
        self.assertTrue(self.state.authenticate("alice", "secret1").ok)

    def test_wrong_password_and_unknown_user_give_same_message(self):
        self.state.register("alice", "secret1")
        wrong = self.state.authenticate("alice", "nope-nope")
        unknown = self.state.authenticate("ghost", "secret1")
        self.assertFalse(wrong.ok)
        self.assertEqual(wrong.message, unknown.message)

    def test_duplicate_username_rejected(self):
        self.state.register("alice", "secret1")
        self.assertFalse(self.state.register("alice", "other-pass").ok)

    def test_weak_or_malformed_credentials_rejected(self):
        self.assertFalse(self.state.register("ab", "secret1").ok)
        self.assertFalse(self.state.register("bad name!", "secret1").ok)
        self.assertFalse(self.state.register("alice", "123").ok)
        self.assertFalse(self.state.register(None, "secret1").ok)

    def test_password_not_stored_in_plain_text(self):
        self.state.register("alice", "secret1")
        stored = self.state._users["alice"]
        self.assertNotIn(b"secret1", stored["hash"])
        self.assertNotEqual(stored["hash"], stored["salt"])


class LockoutTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.state = HotelState(clock=lambda: self.now[0])
        self.state.register("alice", "secret1")

    def fail_times(self, n, name="alice"):
        for _ in range(n):
            self.state.authenticate(name, "wrong-pass")

    def test_account_locks_after_repeated_failures_even_with_correct_password(self):
        self.fail_times(5)
        result = self.state.authenticate("alice", "secret1")
        self.assertFalse(result.ok)
        self.assertIn("Too many", result.message)

    def test_lockout_expires(self):
        self.fail_times(5)
        self.now[0] += 61
        self.assertTrue(self.state.authenticate("alice", "secret1").ok)

    def test_success_resets_failure_count(self):
        self.fail_times(4)
        self.assertTrue(self.state.authenticate("alice", "secret1").ok)
        self.fail_times(4)
        self.assertTrue(self.state.authenticate("alice", "secret1").ok)

    def test_unknown_usernames_lock_out_too(self):
        self.fail_times(5, name="ghost")
        self.assertIn("Too many", self.state.authenticate("ghost", "whatever").message)


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.state = HotelState()

    def test_book_specific_rooms_computes_bill(self):
        result = self.state.book("alice", room_numbers=[1, 9], nights=2)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], (50 + 150) * 2)

    def test_book_by_count_and_type(self):
        result = self.state.book("alice", count=2, room_type="deluxe")
        self.assertEqual([r["room_number"] for r in result.data["rooms"]], [5, 6])

    def test_booking_is_all_or_nothing(self):
        self.state.book("bob", room_numbers=[2])
        result = self.state.book("alice", room_numbers=[1, 2])
        self.assertFalse(result.ok)
        self.assertFalse(self.state.room_status("alice")[0]["is_occupied"])

    def test_cannot_overbook_a_type(self):
        self.assertFalse(self.state.book("alice", count=3, room_type="suite").ok)

    def test_invalid_inputs_rejected(self):
        self.assertFalse(self.state.book("alice", room_numbers=[99]).ok)
        self.assertFalse(self.state.book("alice", count=0).ok)
        self.assertFalse(self.state.book("alice", count=1, nights=0).ok)
        self.assertFalse(self.state.book("alice", count=1, nights=True).ok)
        self.assertFalse(self.state.book("alice", count=1, room_type="penthouse").ok)
        self.assertFalse(self.state.book("alice", room_numbers="1").ok)
        self.assertFalse(self.state.book("alice", count=1, room_type=["suite"]).ok)

    def test_concurrent_booking_of_same_room_has_one_winner(self):
        winners = []
        barrier = threading.Barrier(20)

        def attempt(i):
            barrier.wait()
            if self.state.book(f"user{i}", room_numbers=[1]).ok:
                winners.append(i)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.state = HotelState()
        self.state.book("alice", room_numbers=[1, 2, 9], nights=3)

    def test_checkout_single_room_keeps_the_rest(self):
        result = self.state.checkout("alice", room_numbers=[1])
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total"], 150)
        self.assertEqual(len(self.state.my_rooms("alice").data["rooms"]), 2)

    def test_checkout_all(self):
        result = self.state.checkout("alice")
        self.assertEqual(result.data["total"], (50 + 50 + 150) * 3)
        self.assertEqual(self.state.my_rooms("alice").data["rooms"], [])

    def test_cannot_check_out_someone_elses_room(self):
        self.state.book("bob", room_numbers=[3])
        result = self.state.checkout("alice", room_numbers=[3])
        self.assertFalse(result.ok)
        self.assertTrue(self.state.room_status("bob")[2]["mine"])

    def test_checkout_with_nothing_booked_fails(self):
        self.assertFalse(self.state.checkout("bob").ok)

    def test_room_status_marks_ownership(self):
        status = self.state.room_status("alice")
        self.assertTrue(status[0]["mine"])
        self.assertFalse(self.state.room_status("bob")[0]["mine"])
        self.assertTrue(self.state.room_status("bob")[0]["is_occupied"])


if __name__ == "__main__":
    unittest.main()
