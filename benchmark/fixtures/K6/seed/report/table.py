"""Plain-text rendering for the weekly report."""


def format_cell(value):
    """How one value is shown in a table: None as "-", a float with two decimals, else str()."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
