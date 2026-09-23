"""Contacts to and from plain dictionaries (the storage format)."""

from contacts.model import Contact


def to_dict(contact):
    return {"name": contact.name, "phone": contact.phone}


def from_dict(data):
    return Contact(name=data["name"], phone=data["phone"])
