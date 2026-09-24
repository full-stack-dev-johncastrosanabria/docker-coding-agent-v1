"""Loads app/defaults.ini and checks every value before the app starts."""

import configparser
import os

DEFAULTS = os.path.join(os.path.dirname(__file__), "defaults.ini")
LEVELS = ("debug", "info", "warning", "error")


def load(path=DEFAULTS):
    """The settings as a dict of typed values. Anything invalid raises ValueError."""
    parser = configparser.ConfigParser()
    with open(path, encoding="utf-8") as handle:
        parser.read_file(handle)
    settings = {
        "host": parser.get("server", "host"),
        "port": parser.getint("server", "port"),
        "workers": parser.getint("server", "workers"),
        "cache_enabled": parser.getboolean("cache", "enabled"),
        "cache_ttl_seconds": parser.getint("cache", "ttl_seconds"),
        "log_level": parser.get("logging", "level"),
    }
    if not 1 <= settings["port"] <= 65535:
        raise ValueError(f"port {settings['port']} is out of range")
    if settings["workers"] < 1:
        raise ValueError("workers must be at least 1")
    if settings["cache_ttl_seconds"] <= 0:
        raise ValueError("cache ttl_seconds must be positive")
    if settings["log_level"] not in LEVELS:
        raise ValueError(f"unknown log level {settings['log_level']!r}")
    return settings
