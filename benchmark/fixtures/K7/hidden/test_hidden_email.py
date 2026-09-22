import unittest

from contacts.display import format_contact
from contacts.model import Contact
from contacts.serialize import from_dict, to_dict


class HiddenEmailTest(unittest.TestCase):
    def test_email_defaults_to_none(self):
        self.assertIsNone(Contact(name="Ada", phone="1").email)

    def test_to_dict_includes_email(self):
        self.assertEqual(to_dict(Contact(name="Ada", phone="1", email="ada@example.com")),
                         {"name": "Ada", "phone": "1", "email": "ada@example.com"})
        self.assertEqual(to_dict(Contact(name="Ada", phone="1")),
                         {"name": "Ada", "phone": "1", "email": None})

    def test_from_dict_with_and_without_email(self):
        self.assertEqual(from_dict({"name": "Ada", "phone": "1"}), Contact(name="Ada", phone="1"))
        self.assertEqual(from_dict({"name": "Ada", "phone": "1", "email": "a@b.c"}).email, "a@b.c")

    def test_display(self):
        self.assertEqual(format_contact(Contact(name="Ada Lovelace", phone="555-0100",
                                                email="ada@example.com")),
                         "Ada Lovelace (555-0100) <ada@example.com>")
        self.assertEqual(format_contact(Contact(name="Ada Lovelace", phone="555-0100")),
                         "Ada Lovelace (555-0100)")


if __name__ == "__main__":
    unittest.main()
