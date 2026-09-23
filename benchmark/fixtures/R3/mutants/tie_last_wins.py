"""Small word statistics over plain text. Words are separated by any run of whitespace."""


def word_count(text):
    """Number of words in text. Empty or whitespace-only text has 0 words."""
    return len(text.split())


def longest_word(text):
    """The longest word in text; on a tie, the first one wins. Empty text gives ''."""
    best = ""
    for word in text.split():
        if len(word) >= len(best):
            best = word
    return best
