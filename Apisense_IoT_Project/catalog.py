"""
catalog.py - Apisense Catalog (registry) REST web service, built with CherryPy.

It is the first component to start. Every other actor asks the Catalog for:
  * the MQTT broker address and base topic          (GET /broker)
  * the list of hives with thresholds and devices   (GET /hives)
and registers its devices here to obtain an ID and MQTT topics (POST /devices).

REST API (all bodies are JSON)
-----------------------------------------------------------------------------
GET    /                      full catalog (broker info + hives)
GET    /broker                {"broker", "port", "base_topic"}
GET    /hives                 list of hives
GET    /hives/<hive_id>       one hive
POST   /hives                 create hive  {"hive_id", "vibration_threshold"?, "location"?}
PUT    /hives/<hive_id>       update hive  {"vibration_threshold"?, "location"?}
DELETE /hives/<hive_id>       delete hive
POST   /devices               register device {"hive_id", "kind": sensor|actuator, "device_type"}
                              (idempotent: the same (hive, kind, type) returns the same device)
-----------------------------------------------------------------------------

Run:  python catalog.py
"""
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

import cherrypy

from utils.config import BASE_DIR, load_settings, setup_logging

HIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")  # safe inside MQTT topics
SENSOR_TYPES = ("vibration", "weight", "environment")
ACTUATOR_TYPES = ("deterrent",)
DEFAULT_SENSORS = SENSOR_TYPES
DEFAULT_ACTUATORS = ACTUATOR_TYPES


class CatalogStore:
    """Business logic + JSON persistence. Independent from CherryPy (unit-testable)."""

    def __init__(self, path, broker, port, base_topic, default_threshold=2.5, bootstrap_hives=()):
        self.path = Path(path)
        self.broker_info = {"broker": broker, "port": int(port), "base_topic": base_topic}
        self.default_threshold = float(default_threshold)
        self._lock = threading.RLock()
        first_run = not self.path.exists()
        self.data = self._load()
        # Topics depend on the base topic in settings: keep stored topics in sync
        for hive in self.data["hives"]:
            self._refresh_topics(hive)
        self._save()
        if first_run:  # seed the demo hives listed in settings.json
            for h in bootstrap_hives:
                self.create_hive(h["hive_id"], h.get("vibration_threshold"), h.get("location"))

    # ------------------------------------------------------------ persistence
    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data.get("hives"), list):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"hives": [], "last_update": time.time()}

    def _save(self):
        """Atomic write: never leave a half-written Catalog.json behind."""
        self.data["last_update"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ helpers
    def _topic(self, *parts):
        return "/".join([self.broker_info["base_topic"], *parts])

    def _refresh_topics(self, hive):
        hid = hive["hive_id"]
        hive["alerts_topic"] = self._topic(hid, "alerts")
        for s in hive.get("sensors", []):
            s["topic"] = self._topic(hid, "sensors", s["id"])
        for a in hive.get("actuators", []):
            a["topic"] = self._topic(hid, "actuators", a["id"], "cmd")
            a["status_topic"] = self._topic(hid, "actuators", a["id"], "status")

    def _find(self, hive_id):
        return next((h for h in self.data["hives"] if h["hive_id"] == hive_id), None)

    @staticmethod
    def _validate_threshold(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("vibration_threshold must be a number")
        if not 0 < value <= 100:
            raise ValueError("vibration_threshold must be in (0, 100]")
        return round(value, 3)

    def _new_device(self, hive, kind, device_type):
        prefix, key = ("s", "sensors") if kind == "sensor" else ("a", "actuators")
        used = {d["id"] for d in hive[key]}
        i = 0
        while f"{hive['hive_id']}_{prefix}{i}" in used:  # first free index (safe after deletions)
            i += 1
        device = {"id": f"{hive['hive_id']}_{prefix}{i}", "kind": kind, "type": device_type,
                  "registered": time.time(), "last_seen": None}
        hive[key].append(device)
        self._refresh_topics(hive)
        return device

    # ------------------------------------------------------------ public API
    def snapshot(self):
        with self._lock:
            return {**self.broker_info, **json.loads(json.dumps(self.data))}

    def list_hives(self):
        with self._lock:
            return json.loads(json.dumps(self.data["hives"]))

    def get_hive(self, hive_id):
        with self._lock:
            hive = self._find(hive_id)
            if hive is None:
                raise KeyError(f"Hive '{hive_id}' not found")
            return json.loads(json.dumps(hive))

    def create_hive(self, hive_id, vibration_threshold=None, location=None):
        if not isinstance(hive_id, str) or not HIVE_ID_RE.match(hive_id):
            raise ValueError("hive_id must be 1-32 chars: letters, digits, '_' or '-'")
        threshold = self._validate_threshold(
            self.default_threshold if vibration_threshold is None else vibration_threshold)
        with self._lock:
            if self._find(hive_id):
                raise FileExistsError(f"Hive '{hive_id}' already exists")
            hive = {"hive_id": hive_id, "vibration_threshold": threshold,
                    "location": location or "", "created": time.time(),
                    "sensors": [], "actuators": []}
            # Pre-provision the standard devices so that every service knows
            # the topics immediately, even before the devices come online.
            for t in DEFAULT_SENSORS:
                self._new_device(hive, "sensor", t)
            for t in DEFAULT_ACTUATORS:
                self._new_device(hive, "actuator", t)
            self.data["hives"].append(hive)
            self._save()
            return json.loads(json.dumps(hive))

    def update_hive(self, hive_id, fields):
        with self._lock:
            hive = self._find(hive_id)
            if hive is None:
                raise KeyError(f"Hive '{hive_id}' not found")
            allowed = {"vibration_threshold", "location"}
            unknown = set(fields) - allowed
            if unknown:
                raise ValueError(f"Cannot update field(s): {', '.join(sorted(unknown))}")
            if "vibration_threshold" in fields:
                hive["vibration_threshold"] = self._validate_threshold(fields["vibration_threshold"])
            if "location" in fields:
                hive["location"] = str(fields["location"])
            self._save()
            return json.loads(json.dumps(hive))

    def delete_hive(self, hive_id):
        with self._lock:
            hive = self._find(hive_id)
            if hive is None:
                raise KeyError(f"Hive '{hive_id}' not found")
            self.data["hives"].remove(hive)
            self._save()
            return {"status": "deleted", "hive_id": hive_id}

    def register_device(self, hive_id, kind, device_type):
        if kind not in ("sensor", "actuator"):
            raise ValueError("kind must be 'sensor' or 'actuator'")
        allowed = SENSOR_TYPES if kind == "sensor" else ACTUATOR_TYPES
        if device_type not in allowed:
            raise ValueError(f"device_type for a {kind} must be one of {list(allowed)}")
        with self._lock:
            hive = self._find(hive_id)
            if hive is None:
                raise KeyError(f"Hive '{hive_id}' not found. Create the hive first.")
            key = "sensors" if kind == "sensor" else "actuators"
            device = next((d for d in hive[key] if d["type"] == device_type), None)
            created = device is None
            if created:
                device = self._new_device(hive, kind, device_type)
            device["last_seen"] = time.time()
            self._save()
            return created, {**json.loads(json.dumps(device)), "hive_id": hive_id,
                             "vibration_threshold": hive["vibration_threshold"],
                             **self.broker_info}


# ============================================================== REST layer
def _json_error(status, message, traceback, version):
    cherrypy.response.headers["Content-Type"] = "application/json"
    return json.dumps({"error": message, "status": status})


class CatalogREST:
    exposed = True

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _body():
        raw = cherrypy.request.body.read()
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise cherrypy.HTTPError(400, "Request body is not valid JSON")
        if not isinstance(body, dict):
            raise cherrypy.HTTPError(400, "Request body must be a JSON object")
        return body

    @staticmethod
    def _out(obj, status=200):
        cherrypy.response.status = status
        cherrypy.response.headers["Content-Type"] = "application/json"
        return json.dumps(obj).encode("utf-8")

    def _call(self, fn, *args, status=200):
        try:
            return self._out(fn(*args), status)
        except KeyError as e:
            raise cherrypy.HTTPError(404, str(e).strip("'\""))
        except FileExistsError as e:
            raise cherrypy.HTTPError(409, str(e))
        except ValueError as e:
            raise cherrypy.HTTPError(400, str(e))

    def GET(self, *uri, **params):
        if not uri:
            return self._out(self.store.snapshot())
        if uri == ("broker",):
            return self._out(self.store.broker_info)
        if uri == ("hives",):
            return self._out(self.store.list_hives())
        if len(uri) == 2 and uri[0] == "hives":
            return self._call(self.store.get_hive, uri[1])
        raise cherrypy.HTTPError(404, "Unknown resource")

    def POST(self, *uri, **params):
        body = self._body()
        if uri == ("hives",):
            return self._call(self.store.create_hive, body.get("hive_id"),
                              body.get("vibration_threshold"), body.get("location"), status=201)
        if uri == ("devices",):
            try:
                created, device = self.store.register_device(
                    body.get("hive_id"), body.get("kind"), body.get("device_type"))
            except KeyError as e:
                raise cherrypy.HTTPError(404, str(e).strip("'\""))
            except ValueError as e:
                raise cherrypy.HTTPError(400, str(e))
            return self._out(device, 201 if created else 200)
        raise cherrypy.HTTPError(404, "Unknown resource")

    def PUT(self, *uri, **params):
        if len(uri) == 2 and uri[0] == "hives":
            return self._call(self.store.update_hive, uri[1], self._body())
        raise cherrypy.HTTPError(404, "Unknown resource")

    def DELETE(self, *uri, **params):
        if len(uri) == 2 and uri[0] == "hives":
            return self._call(self.store.delete_hive, uri[1])
        raise cherrypy.HTTPError(404, "Unknown resource")


def build_store(settings):
    catalog_file = Path(settings["catalog"]["file"])
    if not catalog_file.is_absolute():
        catalog_file = BASE_DIR / catalog_file
    return CatalogStore(catalog_file, settings["mqtt"]["broker"], settings["mqtt"]["port"],
                        settings["mqtt"]["base_topic"], settings["defaults"]["vibration_threshold"],
                        settings["catalog"].get("bootstrap_hives", []))


def mount(store):
    conf = {"/": {"request.dispatch": cherrypy.dispatch.MethodDispatcher(),
                  "error_page.default": _json_error}}
    cherrypy.tree.mount(CatalogREST(store), "/", conf)


def main():
    log = setup_logging("Catalog")
    settings = load_settings()
    store = build_store(settings)
    mount(store)
    cherrypy.config.update({
        "server.socket_host": settings["catalog"]["host"],
        "server.socket_port": int(settings["catalog"]["port"]),
        "log.screen": False,
        "engine.autoreload.on": False,
    })
    log.info("Catalog file: %s", store.path)
    log.info("Broker: %s:%s | base topic: %s", store.broker_info["broker"],
             store.broker_info["port"], store.broker_info["base_topic"])
    log.info("REST API listening on %s", settings["catalog"]["url"])
    cherrypy.engine.start()
    cherrypy.engine.block()


if __name__ == "__main__":
    main()
