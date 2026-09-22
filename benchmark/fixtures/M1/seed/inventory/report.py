"""Plain-text stock reports."""


def stock_report(stock):
    """One 'SKU on_hand' line per SKU, sorted by SKU."""
    return [f"{sku} {stock.on_hand(sku)}" for sku in stock.skus()]
