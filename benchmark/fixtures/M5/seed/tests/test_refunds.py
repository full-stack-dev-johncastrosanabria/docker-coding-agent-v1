import unittest

from billing.credit_note import render
from billing.refunds import refund_lines

ORDERS = [
    (1000, [("mug", 500), ("tea", 500)]),
    (101, [("mug", 1), ("tea", 1)]),
    (100, [("mug", 1), ("tea", 1), ("jar", 1)]),
    (2599, [("mug", 1299), ("tea", 650), ("jar", 650)]),
    (7, [("mug", 300), ("tea", 300), ("jar", 300), ("tin", 300)]),
]


class RefundInvariantsTest(unittest.TestCase):
    def test_even_split(self):
        self.assertEqual(refund_lines(1000, ORDERS[0][1]), [("mug", 500), ("tea", 500)])

    def test_refunds_never_exceed_what_was_paid(self):
        for paid, lines in ORDERS:
            with self.subTest(paid=paid):
                self.assertLessEqual(sum(amount for _, amount in refund_lines(paid, lines)), paid)

    def test_credit_note_shows_the_same_amounts(self):
        for paid, lines in ORDERS:
            with self.subTest(paid=paid):
                refunds = refund_lines(paid, lines)
                note = render(paid, lines).splitlines()
                self.assertEqual([row.split()[-1] for row in note[:-1]],
                                 [f"{amount / 100:.2f}" for _, amount in refunds])
                self.assertEqual(note[-1].split()[-1],
                                 f"{sum(amount for _, amount in refunds) / 100:.2f}")
