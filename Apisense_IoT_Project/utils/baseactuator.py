"""
baseactuator.py - Abstract base class shared by ALL actuators.

Life cycle (see run()):
  1. register()   -> POST /devices on the Catalog, receives ID and topics
  2. setup_mqtt() -> subscribes to its command topic  (.../actuators/<id>/cmd)
  3. on each command {"command": "ON"|"OFF", "source": ..., "reason": ...}
     it calls actuate() and publishes its new state on the status topic
     (.../actuators/<id>/status, retained) so that the Telegram bot and other
     services always know the real state of the device.

Concrete actuators only implement actuate(state).
"""
import json
import logging
import threading
import time
from abc import ABC, abstractmethod

from .catalog_client import CatalogClient, CatalogError
from .myMQTT import MyMQTT


class BaseActuator(ABC):
    def __init__(self, device_type, hive_id, catalog_url):
        self.device_type = device_type      # e.g. 'deterrent'
        self.hive_id = hive_id
        self.catalog = CatalogClient(catalog_url)
        self.device_id = None
        self.topic = None                   # command topic
        self.status_topic = None
        self.broker = None
        self.port = None
        self.client = None
        self.state = False
        self.log = logging.getLogger(f"{device_type}-actuator:{hive_id}")

    @property
    def registered(self):
        return self.device_id is not None

    def register(self):
        try:
            info = self.catalog.register_device(self.hive_id, "actuator", self.device_type)
        except CatalogError as e:
            self.log.error("Registration failed: %s", e)
            return False
        self.device_id, self.topic, self.status_topic = info["id"], info["topic"], info["status_topic"]
        self.broker, self.port = info["broker"], info["port"]
        self.log.info("Registered as %s, listening on %s", self.device_id, self.topic)
        return True

    def setup_mqtt(self):
        if not self.registered:
            return False
        self.client = MyMQTT(self.device_id, self.broker, self.port, notifier=self)
        self.client.mySubscribe(self.topic)
        self.client.start()
        self.publish_status("startup")
        return True

    def notify(self, topic, payload):
        """MQTT callback: parse a command message and actuate."""
        try:
            data = json.loads(payload)
            command = str(data.get("command", "")).upper()
        except (json.JSONDecodeError, AttributeError):
            self.log.warning("Ignoring malformed command: %r", payload[:80])
            return
        if command not in ("ON", "OFF"):
            self.log.warning("Unknown command: %r", command)
            return
        source = data.get("source", "unknown")
        self.state = command == "ON"
        self.actuate(self.state)
        self.log.info("Command %s from %s (%s)", command, source, data.get("reason", "-"))
        self.publish_status(source)

    def publish_status(self, source):
        msg = {"hive_id": self.hive_id, "device_id": self.device_id, "type": self.device_type,
               "state": "ON" if self.state else "OFF", "source": source, "timestamp": time.time()}
        self.client.myPublish(self.status_topic, json.dumps(msg), retain=True)

    @abstractmethod
    def actuate(self, state):
        """Drive the physical (here simulated) device. state: True = ON."""

    def cleanup(self):
        if self.client:
            self.client.stop()

    def run(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        if not self.register() or not self.setup_mqtt():
            self.log.error("Cannot start the actuator")
            return
        try:
            while not stop_event.is_set():
                stop_event.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
            self.log.info("Stopped")
