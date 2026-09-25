"""The monthly summary. It formats its own amounts."""


def monthly_summary(months):
    """One row per month. `months` is a sequence of (label, cents)."""
    rows = []
    for label, cents in months:
        rows.append(f"{label} {cents // 100}.{cents % 100}")
    return "\n".join(rows)
