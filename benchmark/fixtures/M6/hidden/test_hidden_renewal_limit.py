"""M6 hidden tests: the third renewal is refused, and the refusal changes nothing."""

import unittest

from library import loans as loans_module
from library.desk import handle_renewal
from library.loans import Loan, renew


#: Two distinct items, so the oracle tests the RULE and not the rule-for-atlas, and so a constant
#: refusal string cannot pass for an interpolated one.
ITEMS = ("atlas", "cartography")


def twice_renewed(item="atlas"):
    return renew(renew(Loan(item)))


class TestTheLimitExists(unittest.TestCase):
    def test_renewal_limit_is_an_exception_in_the_loans_module(self):
        self.assertTrue(issubclass(getattr(loans_module, "RenewalLimit"), Exception))


class TestTheLimitIsEnforced(unittest.TestCase):
    def test_two_renewals_are_allowed(self):
        for item in ITEMS:
            with self.subTest(item=item):
                loan = twice_renewed(item)
                self.assertEqual((loan.weeks, loan.renewals), (6, 2))

    def test_a_third_renewal_is_refused(self):
        for item in ITEMS:
            with self.subTest(item=item):
                with self.assertRaises(loans_module.RenewalLimit):
                    renew(twice_renewed(item))

    def test_a_refused_renewal_leaves_the_loan_untouched(self):
        for item in ITEMS:
            with self.subTest(item=item):
                loan = twice_renewed(item)
                try:
                    renew(loan)
                except loans_module.RenewalLimit:
                    pass
                self.assertEqual((loan.weeks, loan.renewals), (6, 2))


class TestTheDeskSurfacesTheRefusal(unittest.TestCase):
    def test_the_desk_reports_the_refusal_instead_of_raising(self):
        for item in ITEMS:
            with self.subTest(item=item):
                self.assertEqual(handle_renewal(twice_renewed(item)),
                                 f"{item} cannot be renewed again")

    def test_the_desk_does_not_change_a_refused_loan(self):
        for item in ITEMS:
            with self.subTest(item=item):
                loan = twice_renewed(item)
                handle_renewal(loan)
                self.assertEqual((loan.weeks, loan.renewals), (6, 2))

    def test_the_desk_still_renews_when_it_may(self):
        for item in ITEMS:
            with self.subTest(item=item):
                self.assertEqual(handle_renewal(Loan(item)), f"{item} renewed, now 4 weeks")


if __name__ == "__main__":
    unittest.main()
