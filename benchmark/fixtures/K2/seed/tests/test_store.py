import unittest

from todo.store import TodoList


class TestTodoList(unittest.TestCase):
    def test_ids_start_at_one_and_increase(self):
        todos = TodoList()
        self.assertEqual([todos.add("a"), todos.add("b")], [1, 2])

    def test_blank_title_is_rejected(self):
        with self.assertRaises(ValueError):
            TodoList().add("   ")

    def test_completed_items_are_not_pending(self):
        todos = TodoList()
        first = todos.add("write report")
        todos.add("send invoice")
        todos.complete(first)
        self.assertEqual(todos.pending(), ["send invoice"])

    def test_completing_an_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            TodoList().complete(7)


if __name__ == "__main__":
    unittest.main()
