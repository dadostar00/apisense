"""
thingspeak_adaptor.py - MQTT -> ThingSpeak bridge (cloud storage & charts).

* Subscribes to <base>/+/sensors/+ and <base>/+/alerts.
* Maps every quantity to a ThingSpeak channel field using the mapping in
  secrets.json, e.g.

    "thingspeak": {"channels": [{
        "name": "Apisense",
        "write_key": "XXXXXXXXXXXXXXXX",
        "fields": {"Hive_01/temperature": 1, "Hive_01/humidity": 2, "Hive_01/weight": 3,
                   "Hive_02/temperature": 4, "Hive_02/humidity": 5, "Hive_02/weight": 6,
                   "*/alert": 7}
    }]}

  Keys are "<hive_id>/<quantity>"; "*" matches any hive. Quantities are the
  SenML names (vibration, weight, temperature, humidity) plus "alert"
  (1 = intrusion in progress, 0 = resolved).
* ThingSpeak's free tier accepts one update per channel every 15 s, so values
  are buffered (latest value wins) and flushed at most every `min_interval`
  seconds by a background timer.
* With no channel configured it runs in DRY-RUN mode: it logs what it would send.

Run:  python thingspeak_adaptor.py
"""
import json
import logging
import threading
import time

import requests

from utils import senml
from utils.catalog_client import CatalogClient
from utils.config import load_secrets, load_settings, setup_logging
from utils.myMQTT import MyMQTT

log = logging.getLogger("ThingSpeak")
TS_UPDATE_URL = "https://api.thingspeak.com/update.json"


class Channel:
    def __init__(self, name, write_key, fields):
        self.name = name
        self.write_key = write_key
        self.fields = {k: int(v) for k, v in fields.items()}
        self.buffer = {}
        self.last_sent = 0.0

    def field_for(self, hive_id, quantity):
        return self.fields.get(f"{hive_id}/{quantity}") or self.fields.get(f"*/{quantity}")


class ThingSpeakAdaptor:
    def __init__(self, catalog_url, channels, min_interval=16, sender=None):
        self.catalog = CatalogClient(catalog_url)
        self.channels = [Channel(c.get("name", f"channel{i}"), c.get("write_key", ""), c.get("fields", {}))
                         for i, c in enumerate(channels)]
        self.dry_run = not any(c.write_key for c in self.channels)
        self.min_interval = min_interval
        self._send = sender or self._http_send
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.client = None
        self._dry_buffer = {}

    # ------------------------------------------------------------ ingest
    def notify(self, topic, payload):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        hive_id = data.get("hive_id")
        if not hive_id:
            return
        if topic.endswith("/alerts"):
            values = {"alert": 1 if data.get("level") == "alert" else 0}
        else:
            try:
                _, values = senml.parse(data)
            except ValueError:
                return
        self.ingest(hive_id, values)

    def ingest(self, hive_id, values):
        with self._lock:
            if self.dry_run:
                self._dry_buffer.update({f"{hive_id}/{k}": v for k, v in values.items()})
                return
            for ch in self.channels:
                for quantity, value in values.items():
                    field = ch.field_for(hive_id, quantity)
                    if field:
                        ch.buffer[f"field{field}"] = value

    # ------------------------------------------------------------ output
    def _http_send(self, channel, fields):
        r = requests.post(TS_UPDATE_URL, data={"api_key": channel.write_key, **fields}, timeout=10)
        # ThingSpeak answers "0" (or -1) when the update is rejected (e.g. rate limit)
        ok = r.status_code == 200 and r.text.strip() not in ("0", "-1")
        return ok, r.text.strip()[:80]

    def flush(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            if self.dry_run:
                if self._dry_buffer and now - getattr(self, "_dry_last", 0) >= self.min_interval:
                    log.info("[DRY-RUN] would upload: %s", self._dry_buffer)
                    self._dry_buffer, self._dry_last = {}, now
                return
            ready = []
            for ch in self.channels:
                if ch.buffer and ch.write_key and now - ch.last_sent >= self.min_interval:
                    ready.append((ch, ch.buffer))
                    ch.buffer, ch.last_sent = {}, now
        for ch, fields in ready:  # network I/O outside the lock
            try:
                ok, info = self._send(ch, fields)
            except requests.RequestException as e:
                ok, info = False, str(e)
            if ok:
                log.info("Uploaded to '%s': %s", ch.name, fields)
            else:
                log.warning("Upload to '%s' failed (%s); keeping values for next round", ch.name, info)
                with self._lock:
                    ch.buffer = {**fields, **ch.buffer}

    # ------------------------------------------------------------ main loop
    def start(self):
        broker = self.catalog.wait_until_ready()
        base = broker["base_topic"]
        self.client = MyMQTT("ThingSpeakAdaptor", broker["broker"], broker["port"], notifier=self)
        self.client.mySubscribe(f"{base}/+/sensors/+")
        self.client.mySubscribe(f"{base}/+/alerts")
        self.client.start()
        if self.dry_run:
            log.warning("No ThingSpeak write key configured (secrets.json) -> DRY-RUN mode")
        else:
            log.info("Uploading to %d channel(s) every >= %ss", len(self.channels), self.min_interval)

    def run(self):
        self.start()
        try:
            while not self._stop.wait(1):
                self.flush()
        except KeyboardInterrupt:
            pass
        finally:
            self.client.stop()


def main():
    setup_logging("ThingSpeak")
    s, secrets = load_settings(), load_secrets()
    ThingSpeakAdaptor(s["catalog"]["url"], secrets["thingspeak"]["channels"],
                      s["thingspeak"]["min_interval"]).run()


if __name__ == "__main__":
    main()
