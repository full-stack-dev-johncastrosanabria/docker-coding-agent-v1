import unittest

from todo.store import TodoList


class TestHiddenRemove(unittest.TestCase):
    def test_removed_item_is_no_longer_pending(self):
        todos = TodoList()
        todos.add("a")
        second = todos.add("b")
        todos.add("c")
        todos.remove(second)
        self.assertEqual(todos.pending(), ["a", "c"])

    def test_a_done_item_can_be_removed(self):
        todos = TodoList()
        first = todos.add("a")
        todos.complete(first)
        todos.remove(first)
        with self.assertRaises(KeyError):
            todos.complete(first)

    def test_removing_an_unknown_id_raises(self):
        todos = TodoList()
        todos.add("a")
        with self.assertRaises(KeyError):
            todos.remove(9)

    def test_removing_twice_raises(self):
        todos = TodoList()
        first = todos.add("a")
        todos.remove(first)
        with self.assertRaises(KeyError):
            todos.remove(first)

    def test_ids_are_never_reused_after_a_removal(self):
        todos = TodoList()
        todos.add("a")
        second = todos.add("b")
        todos.remove(second)
        self.assertEqual(todos.add("c"), 3)


if __name__ == "__main__":
    unittest.main()
