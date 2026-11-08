"""
alerts_engine.py - Control strategy of Apisense (intrusion / predator detection).

* Subscribes to every sensor topic:            <base>/+/sensors/+
* Reads thresholds, actuators and topics from the Catalog (refreshed
  periodically, and immediately when a message from an unknown hive arrives,
  so new hives are supported without restarting).
* For each hive it runs a small state machine with hysteresis:

        NORMAL --(vibration >= threshold)--------------------------> ALERT
        ALERT  --(clear_after consecutive readings < threshold)----> NORMAL

  On NORMAL -> ALERT it sends {"command": "ON"} to the hive's deterrent(s) and
  publishes an alert on <base>/<hive>/alerts (forwarded by the Telegram bot).
  On ALERT -> NORMAL it sends "OFF" and publishes a "resolved" message.
  Commands are sent only on state changes (no message flood on every reading).

Run:  python alerts_engine.py
"""
import json
import logging
import threading
import time

from utils import senml
from utils.catalog_client import CatalogClient, CatalogError
from utils.config import load_settings, setup_logging
from utils.myMQTT import MyMQTT

log = logging.getLogger("AlertsEngine")


class AlertsEngine:
    def __init__(self, catalog_url, clear_after=3, catalog_refresh=15, publisher=None):
        self.catalog = CatalogClient(catalog_url)
        self.clear_after = clear_after
        self.catalog_refresh = catalog_refresh
        self.client = None
        self.base_topic = None
        # publisher(topic, payload_str) - injectable for unit tests
        self._publish = publisher
        self.hives = {}          # hive_id -> {"threshold", "actuators": [topics], "alerts_topic"}
        self.state = {}          # hive_id -> {"alert": bool, "calm": int}
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        self._stop = threading.Event()

    # ------------------------------------------------------------ catalog
    def load_configuration(self):
        try:
            hives = self.catalog.get_hives()
        except CatalogError as e:
            log.warning("Catalog refresh failed: %s", e)
            return False
        config = {}
        for h in hives:
            config[h["hive_id"]] = {
                "threshold": float(h["vibration_threshold"]),
                "actuators": [a["topic"] for a in h.get("actuators", []) if a.get("type") == "deterrent"],
                "alerts_topic": h["alerts_topic"],
            }
        with self._lock:
            self.hives = config
            for hid in list(self.state):
                if hid not in config:
                    del self.state[hid]
        self._last_refresh = time.time()
        return True

    # ------------------------------------------------------------ MQTT
    def publish(self, topic, payload):
        if self._publish:
            self._publish(topic, payload)
        else:
            self.client.myPublish(topic, payload)

    def notify(self, topic, payload):
        try:
            header, values = senml.parse(payload)
        except ValueError:
            return
        if header.get("type") != "vibration" or "vibration" not in values:
            return  # only vibration drives the intrusion logic
        hive_id = header.get("hive_id")
        if hive_id not in self.hives and time.time() - self._last_refresh > 2:
            self.load_configuration()                     # maybe a brand-new hive
        if hive_id not in self.hives:
            return
        self.process_vibration(hive_id, float(values["vibration"]))

    def process_vibration(self, hive_id, value):
        with self._lock:
            cfg = self.hives[hive_id]
            st = self.state.setdefault(hive_id, {"alert": False, "calm": 0})
            threshold = cfg["threshold"]
            transition = None
            if value >= threshold:
                st["calm"] = 0
                if not st["alert"]:
                    st["alert"], transition = True, "ON"
            elif st["alert"]:
                st["calm"] += 1
                if st["calm"] >= self.clear_after:
                    st["alert"], st["calm"], transition = False, 0, "OFF"

        if transition is None:
            log.debug("%s vibration %.2f g (threshold %.2f)", hive_id, value, threshold)
            return
        now = time.time()
        reason = (f"vibration {value:.2f} g >= threshold {threshold:.2f} g" if transition == "ON"
                  else f"{self.clear_after} calm readings below {threshold:.2f} g")
        for topic in cfg["actuators"]:
            self.publish(topic, json.dumps({"command": transition, "source": "alerts_engine",
                                            "reason": reason, "timestamp": now}))
        alert = {
            "hive_id": hive_id,
            "level": "alert" if transition == "ON" else "resolved",
            "event": "intrusion",
            "value": value,
            "threshold": threshold,
            "timestamp": now,
            "msg": (f"Possible intrusion at {hive_id}: {reason}. Deterrent switched ON."
                    if transition == "ON" else f"{hive_id} is calm again. Deterrent switched OFF."),
        }
        self.publish(cfg["alerts_topic"], json.dumps(alert))
        if transition == "ON":
            log.warning("ALERT %s: %s -> deterrent ON", hive_id, reason)
        else:
            log.info("RESOLVED %s -> deterrent OFF", hive_id)

    # ------------------------------------------------------------ main loop
    def start(self):
        broker = self.catalog.wait_until_ready()
        self.base_topic = broker["base_topic"]
        self.load_configuration()
        self.client = MyMQTT("AlertsEngine", broker["broker"], broker["port"], notifier=self)
        self.client.mySubscribe(f"{self.base_topic}/+/sensors/+")
        self.client.start()
        log.info("Running on %s:%s, monitoring %d hive(s): %s", broker["broker"], broker["port"],
                 len(self.hives), ", ".join(f"{h} (thr {c['threshold']} g)" for h, c in self.hives.items()))

    def stop(self):
        self._stop.set()
        if self.client:
            self.client.stop()

    def run(self):
        self.start()
        try:
            while not self._stop.wait(self.catalog_refresh):
                self.load_configuration()  # picks up new hives / changed thresholds
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main():
    setup_logging("AlertsEngine")
    s = load_settings()
    AlertsEngine(s["catalog"]["url"], s["alerts"]["clear_after"], s["alerts"]["catalog_refresh"]).run()


if __name__ == "__main__":
    main()
