"""Parse human-entered money amounts into integer cents."""


def parse_amount(text):
    """'1,234.50' -> 123450, '12' -> 1200, '0.5' -> 50. Commas are thousands separators."""
    whole, _, fraction = text.strip().partition(".")
    whole = whole.replace(",", "") or "0"
    if len(fraction) > 2:
        raise ValueError(f"more than two decimal places: {text!r}")
    return int(whole) * 100 + int(fraction or "0")
