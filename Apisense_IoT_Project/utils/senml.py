"""
senml.py - Helpers to build and parse the SenML-like JSON payloads used on MQTT.

Every sensor message has this shape:

    {
      "bn": "apisense/Hive_01/sensors/Hive_01_s2",   # base name = topic
      "hive_id": "Hive_01",
      "device_id": "Hive_01_s2",
      "type": "environment",
      "e": [
        {"n": "temperature", "u": "Cel", "t": 1736500000.0, "v": 34.6},
        {"n": "humidity",    "u": "%RH", "t": 1736500000.0, "v": 61.2}
      ]
    }

Using a list of events means a single message can carry several quantities
(e.g. temperature + humidity) and every consumer parses them the same way.
"""
import json
import time


def build(base_name, hive_id, device_id, device_type, measures, timestamp=None):
    """measures: iterable of (name, unit, value)."""
    t = time.time() if timestamp is None else timestamp
    return json.dumps({
        "bn": base_name,
        "hive_id": hive_id,
        "device_id": device_id,
        "type": device_type,
        "e": [{"n": n, "u": u, "t": t, "v": v} for (n, u, v) in measures],
    })


def parse(payload):
    """Return (header_dict, {name: value}) or raise ValueError on bad payloads."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    if not isinstance(data, dict) or not isinstance(data.get("e"), list):
        raise ValueError("not a SenML payload")
    values = {}
    for event in data["e"]:
        if isinstance(event, dict) and "n" in event and "v" in event:
            values[event["n"]] = event["v"]
    return data, values
