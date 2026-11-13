"""Payment terms: when an invoice falls due."""

from core.timeutil.workdays import add_business_days

NET_DAYS = {"net5": 5, "net10": 10, "net15": 15}


def due_date(issued, terms="net10"):
    """The business day an invoice issued on `issued` falls due under `terms`."""
    return add_business_days(issued, NET_DAYS[terms])
