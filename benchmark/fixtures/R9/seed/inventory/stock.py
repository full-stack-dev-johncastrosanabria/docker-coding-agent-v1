"""Stock levels per SKU."""


def _positive(qty):
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        raise ValueError(f"quantity must be a positive integer, got {qty!r}")


class Stock:
    def __init__(self):
        self._on_hand = {}

    def receive(self, sku, qty):
        _positive(qty)
        self._on_hand[sku] = self._on_hand.get(sku, 0) + qty

    def on_hand(self, sku):
        return self._on_hand.get(sku, 0)

    def skus(self):
        return sorted(self._on_hand)

    def ship(self, sku, qty):
        _positive(qty)
        if qty > self.on_hand(sku):
            raise ValueError(f"only {self.on_hand(sku)} of {sku} on hand")
        self._on_hand[sku] -= qty
