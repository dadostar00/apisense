"""
basesensor.py - Abstract base class shared by ALL sensors.

Life cycle of a sensor (see run()):
  1. register()   -> POST /devices on the Catalog, receives ID, topic and broker
  2. setup_mqtt() -> connects to the broker returned by the Catalog
  3. loop: sense() -> publish() as a SenML message, every `interval` seconds

Concrete sensors only implement sense(), returning a list of
(name, unit, value) tuples.
"""
import logging
import threading
from abc import ABC, abstractmethod

from . import senml
from .catalog_client import CatalogClient, CatalogError
from .myMQTT import MyMQTT


class BaseSensor(ABC):
    def __init__(self, device_type, hive_id, catalog_url, interval=5):
        self.device_type = device_type      # 'vibration' | 'weight' | 'environment'
        self.hive_id = hive_id              # e.g. 'Hive_01'
        self.interval = interval
        self.catalog = CatalogClient(catalog_url)
        self.device_id = None               # assigned by the Catalog, e.g. 'Hive_01_s0'
        self.topic = None                   # e.g. 'apisense/Hive_01/sensors/Hive_01_s0'
        self.broker = None
        self.port = None
        self.client = None
        self.log = logging.getLogger(f"{device_type}-sensor:{hive_id}")

    @property
    def registered(self):
        return self.device_id is not None

    def register(self):
        try:
            info = self.catalog.register_device(self.hive_id, "sensor", self.device_type)
        except CatalogError as e:
            self.log.error("Registration failed: %s", e)
            return False
        self.device_id, self.topic = info["id"], info["topic"]
        self.broker, self.port = info["broker"], info["port"]
        self.log.info("Registered as %s -> %s", self.device_id, self.topic)
        return True

    def setup_mqtt(self):
        if not self.registered:
            return False
        self.client = MyMQTT(self.device_id, self.broker, self.port)
        self.client.start()
        return True

    @abstractmethod
    def sense(self):
        """Return a list of (name, unit, value) tuples."""

    def publish(self, measures):
        payload = senml.build(self.topic, self.hive_id, self.device_id, self.device_type, measures)
        self.client.myPublish(self.topic, payload)
        self.log.info(", ".join(f"{n}={v} {u}" for n, u, v in measures))

    def cleanup(self):
        if self.client:
            self.client.stop()

    def run(self, stop_event=None):
        """Blocking main loop. Stops when stop_event is set (or on Ctrl+C)."""
        stop_event = stop_event or threading.Event()
        if not self.register() or not self.setup_mqtt():
            self.log.error("Cannot start the sensor")
            return
        try:
            while not stop_event.is_set():
                self.publish(self.sense())
                stop_event.wait(self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()
            self.log.info("Stopped")
