"""Parsing setting values given as text (environment variables, command-line flags)."""


def parse_bool(text):
    """"true" is True and "false" is False. Anything else raises ValueError."""
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"not a boolean: {text!r}")


def parse_port(text):
    """A TCP port number, 1 to 65535. Anything else raises ValueError."""
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port
