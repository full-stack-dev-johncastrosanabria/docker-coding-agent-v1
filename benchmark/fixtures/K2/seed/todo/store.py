"""An in-memory to-do list."""


class TodoList:
    """Items keep the order they were added in. Ids start at 1 and are never reused."""

    def __init__(self):
        self._items = {}
        self._next_id = 1

    def add(self, title):
        """Add an item and return its id. A blank title raises ValueError."""
        if not title.strip():
            raise ValueError("title must not be blank")
        item_id = self._next_id
        self._next_id += 1
        self._items[item_id] = {"title": title.strip(), "done": False}
        return item_id

    def complete(self, item_id):
        """Mark an item done. An unknown id raises KeyError."""
        self._items[item_id]["done"] = True

    def pending(self):
        """Titles of the items not yet done, in the order they were added."""
        return [item["title"] for item in self._items.values() if not item["done"]]
