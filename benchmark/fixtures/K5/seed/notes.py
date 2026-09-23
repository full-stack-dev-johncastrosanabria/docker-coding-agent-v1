"""notes: print the notes in a file, numbered, one per line.

Usage: python3 notes.py [FILE]    (FILE defaults to notes.txt)
"""

import sys


def main(argv):
    path = argv[1] if len(argv) > 1 else "notes.txt"
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            print(f"{number}. {line.rstrip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
