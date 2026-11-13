import unittest

from billing.credit_note import render
from billing.refunds import refund_lines


def amounts(paid, cents):
    return [a for _, a in refund_lines(paid, [(f"l{i}", c) for i, c in enumerate(cents)])]


class HiddenRefundTest(unittest.TestCase):
    def test_shares_round_half_up(self):
        self.assertEqual(amounts(200, [1, 1, 1, 5]), [25, 25, 25, 125])
        self.assertEqual(amounts(10, [3, 1]), [8, 2])          # 7.5 -> 8, the last line absorbs
        self.assertEqual(amounts(100, [2, 1]), [67, 33])       # 66.67 -> 67, 33.33 -> 33

    def test_the_last_line_absorbs_an_overshoot(self):
        self.assertEqual(amounts(101, [1, 1]), [51, 50])
        self.assertEqual(amounts(7, [300, 300, 300, 300]), [2, 2, 2, 1])

    def test_never_more_than_paid_and_never_negative(self):
        for paid in range(0, 60):
            for cents in ([1, 1], [1, 1, 1], [5, 3, 2], [1, 1, 1, 1, 1, 1, 1]):
                with self.subTest(paid=paid, cents=cents):
                    got = amounts(paid, cents)
                    self.assertLessEqual(sum(got), paid)
                    self.assertTrue(all(a >= 0 for a in got))

    def test_credit_note_agrees_on_an_overshooting_order(self):
        note = render(101, [("mug", 1), ("tea", 1)]).splitlines()
        self.assertEqual([row.split()[-1] for row in note], ["0.51", "0.50", "1.01"])
