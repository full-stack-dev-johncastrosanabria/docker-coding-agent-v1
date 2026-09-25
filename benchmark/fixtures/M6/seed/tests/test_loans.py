import unittest

from library.desk import handle_renewal
from library.loans import Loan, renew


class TestRenewals(unittest.TestCase):
    def test_a_renewal_extends_the_loan(self):
        loan = renew(Loan("atlas"))
        self.assertEqual((loan.weeks, loan.renewals), (4, 1))

    def test_a_second_renewal_extends_it_again(self):
        loan = renew(renew(Loan("atlas")))
        self.assertEqual((loan.weeks, loan.renewals), (6, 2))

    def test_the_desk_reports_a_renewal(self):
        self.assertEqual(handle_renewal(Loan("atlas")), "atlas renewed, now 4 weeks")


if __name__ == "__main__":
    unittest.main()
