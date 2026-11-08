"""Unit tests of the control logic: Alerts Engine, ThingSpeak adaptor, Telegram bot, sensors."""
import json

import pytest

from alerts_engine import AlertsEngine
from devices.sensors import EnvSensor, VibrationSensor, WeightSensor
from telegram_bot import BotLogic
from thingspeak_adaptor import ThingSpeakAdaptor
from utils import senml
from utils.catalog_client import CatalogError


# ---------------------------------------------------------------- helpers
class FakeCatalog:
    def __init__(self):
        self.hives = {"H1": self._hive("H1", 2.5)}

    @staticmethod
    def _hive(hid, thr):
        return {"hive_id": hid, "vibration_threshold": thr, "location": "",
                "alerts_topic": f"b/{hid}/alerts",
                "sensors": [{"id": f"{hid}_s0", "type": "vibration", "topic": f"b/{hid}/sensors/{hid}_s0"}],
                "actuators": [{"id": f"{hid}_a0", "type": "deterrent", "topic": f"b/{hid}/actuators/{hid}_a0/cmd"}]}

    def get_hives(self):
        return list(self.hives.values())

    def get_hive(self, hid):
        if hid not in self.hives:
            raise CatalogError(f"Hive '{hid}' not found")
        return self.hives[hid]

    def update_hive(self, hid, **fields):
        self.get_hive(hid).update(fields)
        return self.hives[hid]

    def create_hive(self, hid, thr=None):
        self.hives[hid] = self._hive(hid, thr or 2.5)
        return self.hives[hid]


def vib(hid, value):
    return senml.build(f"b/{hid}/sensors/{hid}_s0", hid, f"{hid}_s0", "vibration", [("vibration", "g", value)])


@pytest.fixture
def engine():
    sent = []
    e = AlertsEngine("http://unused", clear_after=3, publisher=lambda t, p: sent.append((t, json.loads(p))))
    e.catalog = FakeCatalog()
    e.load_configuration()
    return e, sent


# ---------------------------------------------------------------- alerts engine
def test_alert_on_threshold_crossing(engine):
    e, sent = engine
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.5))
    assert sent == []                                    # calm: nothing sent
    e.notify("b/H1/sensors/H1_s0", vib("H1", 3.1))
    topics = [t for t, _ in sent]
    assert topics == ["b/H1/actuators/H1_a0/cmd", "b/H1/alerts"]
    assert sent[0][1]["command"] == "ON" and sent[1][1]["level"] == "alert"


def test_no_flood_and_hysteresis(engine):
    e, sent = engine
    for v in (3.0, 4.0, 3.5):                            # still in alert: only 1 ON
        e.notify("b/H1/sensors/H1_s0", vib("H1", v))
    assert sum(1 for _, m in sent if m.get("command") == "ON") == 1
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.4))
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.4))
    e.notify("b/H1/sensors/H1_s0", vib("H1", 3.3))       # new spike resets the calm counter
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.4))
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.4))
    assert not any(m.get("command") == "OFF" for _, m in sent)
    e.notify("b/H1/sensors/H1_s0", vib("H1", 0.4))       # 3rd consecutive calm reading
    assert [m.get("command") for _, m in sent if "command" in m] == ["ON", "OFF"]
    assert sent[-1][1]["level"] == "resolved"


def test_threshold_and_new_hive_follow_catalog(engine):
    e, sent = engine
    e.catalog.hives["H1"]["vibration_threshold"] = 4.0
    e.catalog.create_hive("H2", 1.0)
    e.load_configuration()
    e.notify("b/H1/sensors/H1_s0", vib("H1", 3.5))       # below the new threshold
    e._last_refresh = 0
    e.notify("b/H2/sensors/H2_s0", vib("H2", 1.2))       # new hive is handled
    assert sent[0][0] == "b/H2/actuators/H2_a0/cmd"
    assert len(sent) == 2


def test_engine_ignores_garbage_and_other_sensors(engine):
    e, sent = engine
    e.notify("b/H1/sensors/H1_s1", "not json")
    e.notify("b/H1/sensors/H1_s1", json.dumps({"value": 5}))
    e.notify("b/H1/sensors/H1_s1", senml.build("x", "H1", "H1_s1", "weight", [("weight", "kg", 99)]))
    e.notify("b/X/sensors/X_s0", vib("X", 9))            # unknown hive
    assert sent == []


# ---------------------------------------------------------------- thingspeak
def test_thingspeak_mapping_and_rate_limit():
    uploads = []
    ch = {"name": "c", "write_key": "K", "fields": {"H1/temperature": 1, "H1/weight": 3, "*/alert": 7}}
    ts = ThingSpeakAdaptor("http://unused", [ch], min_interval=15,
                           sender=lambda c, f: (uploads.append(dict(f)) or (True, "1")))
    ts.notify("b/H1/sensors/H1_s2", senml.build("x", "H1", "H1_s2", "environment",
                                                [("temperature", "Cel", 34.1), ("humidity", "%RH", 60)]))
    ts.notify("b/H1/sensors/H1_s1", senml.build("x", "H1", "H1_s1", "weight", [("weight", "kg", 30.5)]))
    ts.notify("b/H9/alerts", json.dumps({"hive_id": "H9", "level": "alert"}))
    ts.flush(now=1000)
    assert uploads == [{"field1": 34.1, "field3": 30.5, "field7": 1}]   # humidity not mapped
    ts.notify("b/H1/sensors/H1_s1", senml.build("x", "H1", "H1_s1", "weight", [("weight", "kg", 31)]))
    ts.flush(now=1005)                                   # too early (rate limit)
    assert len(uploads) == 1
    ts.flush(now=1016)
    assert uploads[-1] == {"field3": 31}


def test_thingspeak_failed_upload_is_retried():
    calls = []
    ts = ThingSpeakAdaptor("http://unused", [{"write_key": "K", "fields": {"*/weight": 1}}], min_interval=15,
                           sender=lambda c, f: (calls.append(dict(f)) or (len(calls) > 1, "0")))
    ts.ingest("H1", {"weight": 30})
    ts.flush(now=100)
    ts.flush(now=120)
    assert calls == [{"field1": 30}, {"field1": 30}]


def test_thingspeak_dry_run_without_key():
    ts = ThingSpeakAdaptor("http://unused", [], min_interval=15)
    assert ts.dry_run
    ts.ingest("H1", {"weight": 30})
    ts.flush(now=100)                                    # must not raise / call network


# ---------------------------------------------------------------- telegram bot logic
@pytest.fixture
def bot():
    sent = []
    b = BotLogic(FakeCatalog(), lambda t, p: sent.append((t, json.loads(p))), authorized_users=["42"])
    return b, sent


def test_bot_authorization(bot):
    b, sent = bot
    text, _ = b.handle(7, 7, "/on H1")
    assert "not authorized" in text and sent == []


def test_bot_actuation_uses_catalog_topic(bot):
    b, sent = bot
    text, _ = b.handle(42, 42, "/on H1")
    assert text == "Command ON sent to the deterrent of H1."
    assert len(sent) == 1 and sent[0][0] == "b/H1/actuators/H1_a0/cmd" and sent[0][1]["command"] == "ON"
    b.handle(42, 42, "/deactivate H1")
    assert sent[1][1]["command"] == "OFF" and sent[1][1]["source"] == "telegram"
    assert "Error" in b.handle(42, 42, "/on NOPE")[0]
    assert b.handle_button(42, "off:H1").startswith("Command OFF")


def test_bot_threshold_addhive_hives(bot):
    b, _ = bot
    assert "set to 3.3" in b.handle(42, 42, "/threshold H1 3.3")[0]
    assert "must be a number" in b.handle(42, 42, "/threshold H1 abc")[0]
    assert "created" in b.handle(42, 42, "/addhive H5 2")[0]
    text, buttons = b.handle(42, 42, "/hives")
    assert "H5" in text and buttons[1][0][1] == "on:H5"


def test_bot_status_and_alert_forwarding(bot):
    b, _ = bot
    assert b.on_mqtt("b/H1/sensors/H1_s1",
                     senml.build("x", "H1", "H1_s1", "weight", [("weight", "kg", 31.2)])) is None
    b.on_mqtt("b/H1/actuators/H1_a0/status", json.dumps({"hive_id": "H1", "state": "ON", "source": "alerts_engine"}))
    alert = b.on_mqtt("b/H1/alerts", json.dumps({"hive_id": "H1", "level": "alert", "msg": "Possible intrusion"}))
    assert "Possible intrusion" in alert
    status, _ = b.handle(42, 42, "/status H1")
    assert "weight: 31.2 kg" in status and "Deterrent: ON" in status and "Alert: ACTIVE" in status


def test_bot_start_stop_persists_chats(tmp_path):
    f = tmp_path / "chats.json"
    b = BotLogic(FakeCatalog(), lambda t, p: None, [], chats_file=f)
    b.handle(1, 100, "/start")
    assert BotLogic(FakeCatalog(), None, [], chats_file=f).chats == {"100"}
    b.handle(1, 100, "/stop")
    assert BotLogic(FakeCatalog(), None, [], chats_file=f).chats == set()


# ---------------------------------------------------------------- sensors simulation
def test_sensor_simulations_are_plausible():
    v = VibrationSensor("H1", "http://unused", intrusion_probability=0.0)
    assert all(0 <= v.sense()[0][2] < 2.5 for _ in range(200))     # calm hive: no false alarms
    v = VibrationSensor("H1", "http://unused", intrusion_probability=1.0)
    assert v.sense()[0][2] >= 3.2                                   # intrusion burst
    w = WeightSensor("H1", "http://unused")
    weights = [w.sense()[0][2] for _ in range(50)]
    assert all(15 <= x <= 80 for x in weights) and max(weights) - min(weights) < 5
    env = EnvSensor("H1", "http://unused").sense()
    assert [n for n, _, _ in env] == ["temperature", "humidity"]
    assert 30 < env[0][2] < 40 and 20 <= env[1][2] <= 95
