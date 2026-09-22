import unittest

from contacts.display import format_contact
from contacts.model import Contact
from contacts.serialize import from_dict, to_dict


class ContactsTest(unittest.TestCase):
    def test_round_trip(self):
        contact = Contact(name="Ada Lovelace", phone="555-0100")
        self.assertEqual(from_dict(to_dict(contact)), contact)

    def test_stored_fields(self):
        data = to_dict(Contact(name="Ada Lovelace", phone="555-0100"))
        self.assertEqual(data["name"], "Ada Lovelace")
        self.assertEqual(data["phone"], "555-0100")

    def test_display(self):
        self.assertEqual(format_contact(Contact(name="Ada Lovelace", phone="555-0100")),
                         "Ada Lovelace (555-0100)")


if __name__ == "__main__":
    unittest.main()
