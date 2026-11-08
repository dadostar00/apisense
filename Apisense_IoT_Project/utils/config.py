"""
config.py - Centralised configuration loading for every Apisense component.

Two files live next to the scripts (in ``Apisense_IoT_Project/``):

* ``settings.json``  - non-secret settings (catalog address, MQTT broker,
                       publishing intervals, alert tuning). Committed to git.
* ``secrets.json``   - API keys and tokens (Telegram, ThingSpeak).
                       NEVER committed: copy ``secrets.example.json`` and fill it in.

Every value can also be overridden with an environment variable, which is
handy for tests and for running the system on another machine:

    APISENSE_CATALOG_URL, APISENSE_CATALOG_HOST, APISENSE_CATALOG_PORT,
    APISENSE_BROKER, APISENSE_BROKER_PORT, APISENSE_BASE_TOPIC,
    APISENSE_CATALOG_FILE, APISENSE_SECRETS_FILE,
    TELEGRAM_TOKEN, TELEGRAM_AUTHORIZED_USERS (comma separated)
"""
import json
import logging
import os
from pathlib import Path

# Directory that contains catalog.py, settings.json, Catalog.json, ...
BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_SETTINGS = {
    "catalog": {"host": "127.0.0.1", "port": 8080, "file": "Catalog.json"},
    "mqtt": {"broker": "broker.hivemq.com", "port": 1883, "base_topic": "apisense"},
    "defaults": {"vibration_threshold": 2.5},
    "sensors": {
        "vibration_interval": 5,
        "weight_interval": 15,
        "environment_interval": 15,
        "intrusion_probability": 0.03,
    },
    "alerts": {"clear_after": 3, "catalog_refresh": 15},
    "managers": {"discovery_interval": 10},
    "thingspeak": {"min_interval": 16},
}


def _deep_update(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON in {path}: {e}")


def load_settings():
    """Return the merged settings (defaults <- settings.json <- env vars)."""
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    _deep_update(settings, _read_json(BASE_DIR / "settings.json"))

    env = os.environ
    if "APISENSE_CATALOG_HOST" in env:
        settings["catalog"]["host"] = env["APISENSE_CATALOG_HOST"]
    if "APISENSE_CATALOG_PORT" in env:
        settings["catalog"]["port"] = int(env["APISENSE_CATALOG_PORT"])
    if "APISENSE_CATALOG_FILE" in env:
        settings["catalog"]["file"] = env["APISENSE_CATALOG_FILE"]
    if "APISENSE_BROKER" in env:
        settings["mqtt"]["broker"] = env["APISENSE_BROKER"]
    if "APISENSE_BROKER_PORT" in env:
        settings["mqtt"]["port"] = int(env["APISENSE_BROKER_PORT"])
    if "APISENSE_BASE_TOPIC" in env:
        settings["mqtt"]["base_topic"] = env["APISENSE_BASE_TOPIC"]
    if "APISENSE_INTRUSION_PROBABILITY" in env:
        settings["sensors"]["intrusion_probability"] = float(env["APISENSE_INTRUSION_PROBABILITY"])

    settings["catalog"]["url"] = env.get(
        "APISENSE_CATALOG_URL",
        f"http://{settings['catalog']['host']}:{settings['catalog']['port']}",
    ).rstrip("/")
    return settings


def load_secrets():
    """Return the secrets dictionary (secrets.json <- env vars)."""
    path = Path(os.environ.get("APISENSE_SECRETS_FILE", BASE_DIR / "secrets.json"))
    secrets = {
        "telegram": {"token": "", "authorized_users": [], "alert_chat_ids": []},
        "thingspeak": {"channels": []},
    }
    _deep_update(secrets, _read_json(path))

    if os.environ.get("TELEGRAM_TOKEN"):
        secrets["telegram"]["token"] = os.environ["TELEGRAM_TOKEN"]
    if os.environ.get("TELEGRAM_AUTHORIZED_USERS"):
        secrets["telegram"]["authorized_users"] = [
            u.strip() for u in os.environ["TELEGRAM_AUTHORIZED_USERS"].split(",") if u.strip()
        ]
    # Normalise IDs to strings so comparisons with Telegram IDs are consistent
    secrets["telegram"]["authorized_users"] = [str(u) for u in secrets["telegram"]["authorized_users"]]
    secrets["telegram"]["alert_chat_ids"] = [str(c) for c in secrets["telegram"]["alert_chat_ids"]]
    return secrets


def setup_logging(component):
    """Configure a uniform, ASCII-safe log format and return a named logger."""
    logging.basicConfig(
        level=os.environ.get("APISENSE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Third-party libraries are very chatty at INFO level
    logging.getLogger("cherrypy.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("TeleBot").setLevel(logging.WARNING)
    return logging.getLogger(component)
