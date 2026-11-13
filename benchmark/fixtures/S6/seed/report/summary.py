"""Sales summary: total amount per region, as CSV text."""

import csv
import io


def summarize(path):
    totals = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            totals[row["region"]] = totals.get(row["region"], 0) + int(row["amount"])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["region", "total"])
    for region in sorted(totals):
        writer.writerow([region, totals[region]])
    return out.getvalue()
