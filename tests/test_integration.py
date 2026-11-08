"""
End-to-end test over a REAL MQTT broker (Mosquitto on localhost:1883).
Skipped automatically if no local broker is running.

Flow verified:
  Catalog (REST) -> VibrationSensor publishes over MQTT -> AlertsEngine detects
  the intrusion -> DeterrentActuator is switched ON and publishes its status ->
  alert message published (as forwarded by Telegram) -> data reaches the
  ThingSpeak adaptor -> hive calms down -> deterrent OFF + "resolved" message.
"""
import json
import threading
import time

import pytest

from alerts_engine import AlertsEngine
from conftest import TEST_BROKER, TEST_BROKER_PORT, broker_available
from devices.actuators import DeterrentActuator
from devices.sensors import VibrationSensor, WeightSensor
from telegram_bot import BotLogic
from thingspeak_adaptor import ThingSpeakAdaptor
from utils.catalog_client import CatalogClient
from utils.device_manager import HiveDeviceManager
from utils.myMQTT import MyMQTT

pytestmark = pytest.mark.skipif(not broker_available(), reason="no MQTT broker on localhost:1883")


class Recorder:
    def __init__(self):
        self.messages = []

    def notify(self, topic, payload):
        self.messages.append((topic, json.loads(payload)))

    def wait_for(self, predicate, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if any(predicate(t, m) for t, m in list(self.messages)):
                return True
            time.sleep(0.1)
        return False


def wait_until(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_end_to_end_intrusion_flow(catalog_url, base_topic):
    CatalogClient(catalog_url).create_hive("E2E", 2.5)

    rec = Recorder()
    spy = MyMQTT("test-spy", TEST_BROKER, TEST_BROKER_PORT, notifier=rec)
    spy.mySubscribe(f"{base_topic}/#")
    assert spy.start()

    engine = AlertsEngine(catalog_url, clear_after=2, catalog_refresh=60)
    engine.start()
    uploads = []
    ts = ThingSpeakAdaptor(catalog_url, [{"write_key": "K", "fields": {"E2E/vibration": 1, "*/alert": 2}}],
                           min_interval=0, sender=lambda c, f: (uploads.append(dict(f)) or (True, "1")))
    ts.start()
    bot = BotLogic(CatalogClient(catalog_url), spy.myPublish, [])
    bot_rec = MyMQTT("test-bot", TEST_BROKER, TEST_BROKER_PORT,
                     notifier=type("N", (), {"notify": lambda s, t, p: bot.on_mqtt(t, p)})())
    bot_rec.mySubscribe(f"{base_topic}/+/#")
    bot_rec.start()

    stop = threading.Event()
    actuator = DeterrentActuator("E2E", catalog_url)
    threading.Thread(target=actuator.run, args=(stop,), daemon=True).start()
    assert wait_until(lambda: actuator.client is not None and actuator.client.is_connected)

    sensor = VibrationSensor("E2E", catalog_url, interval=0.3, intrusion_probability=1.0)
    threading.Thread(target=sensor.run, args=(stop,), daemon=True).start()
    try:
        # 1) intrusion -> actuator ON + alert published
        assert wait_until(lambda: actuator.state is True), "deterrent was not switched ON"
        assert rec.wait_for(lambda t, m: t == f"{base_topic}/E2E/alerts" and m["level"] == "alert")
        assert rec.wait_for(lambda t, m: t.endswith("/E2E_a0/status") and m["state"] == "ON"
                            and m["source"] == "alerts_engine")
        # 2) the alert reaches the Telegram bot logic and ThingSpeak
        assert wait_until(lambda: bot.alerts.get("E2E", {}).get("level") == "alert")
        for _ in range(20):
            ts.flush()
            if any(u.get("field2") == 1 for u in uploads):
                break
            time.sleep(0.2)
        assert any("field1" in u for u in uploads) and any(u.get("field2") == 1 for u in uploads)

        # 3) hive calms down -> OFF + resolved
        sensor.intrusion_probability = 0.0
        assert wait_until(lambda: actuator.state is False, timeout=20), "deterrent was not switched OFF"
        assert rec.wait_for(lambda t, m: t == f"{base_topic}/E2E/alerts" and m["level"] == "resolved")

        # 4) manual command from the Telegram bot logic
        assert "Command ON sent" in bot.handle(1, 1, "/on E2E")[0]
        assert wait_until(lambda: actuator.state is True)
        assert rec.wait_for(lambda t, m: t.endswith("/E2E_a0/status") and m["source"] == "telegram")
        status, _ = bot.handle(1, 1, "/status E2E")
        assert "vibration:" in status and "Deterrent: ON (by telegram)" in status
    finally:
        stop.set()
        engine.stop()
        ts.client.stop()
        spy.stop()
        bot_rec.stop()


def test_device_manager_hot_plug(catalog_url, base_topic):
    """A hive created at runtime gets its devices started; deleting it stops them."""
    catalog = CatalogClient(catalog_url)
    rec = Recorder()
    spy = MyMQTT("test-spy2", TEST_BROKER, TEST_BROKER_PORT, notifier=rec)
    spy.mySubscribe(f"{base_topic}/HOT/sensors/+")
    spy.start()

    mgr = HiveDeviceManager("test-mgr", catalog_url, lambda h: [WeightSensor(h["hive_id"], catalog_url, 0.3)]
                            if h["hive_id"] == "HOT" else [], discovery_interval=0.5)
    t = threading.Thread(target=mgr.run, daemon=True)
    t.start()
    try:
        catalog.create_hive("HOT")
        assert rec.wait_for(lambda tp, m: m["type"] == "weight" and m["e"][0]["u"] == "kg")
        catalog.delete_hive("HOT")
        assert wait_until(lambda: "HOT" not in mgr.running)
    finally:
        mgr.stop()
        spy.stop()
