"""The contact record."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Contact:
    name: str
    phone: str
