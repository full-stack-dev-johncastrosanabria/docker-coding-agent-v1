"""Load and validate the service configuration shipped in service/config.json."""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
LOG_LEVELS = ("debug", "info", "warning", "error")
FEATURES = ("audit", "metrics")


def load_settings(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as handle:
        settings = json.load(handle)
    validate(settings)
    return settings


def validate(settings):
    if not isinstance(settings.get("port"), int) or not 1 <= settings["port"] <= 65535:
        raise ValueError("port must be an integer in 1..65535")
    if not isinstance(settings.get("timeout_seconds"), int) or not 1 <= settings["timeout_seconds"] <= 120:
        raise ValueError("timeout_seconds must be an integer in 1..120")
    if settings.get("log_level") not in LOG_LEVELS:
        raise ValueError(f"log_level must be one of {LOG_LEVELS}")
    features = settings.get("features")
    if not isinstance(features, dict) or set(features) != set(FEATURES):
        raise ValueError(f"features must set exactly {FEATURES}")
    if not all(isinstance(value, bool) for value in features.values()):
        raise ValueError("every feature flag must be true or false")
