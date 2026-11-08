"""
device_manager.py - Runs the simulated devices of every hive listed in the Catalog.

It periodically polls GET /hives and:
  * starts the devices (one thread each) of every NEW hive -> hives added while
    the system is running are picked up automatically ("hot plug");
  * stops the devices of hives that were DELETED from the Catalog.
"""
import logging
import threading

from .catalog_client import CatalogClient, CatalogError


class HiveDeviceManager:
    def __init__(self, name, catalog_url, device_factory, discovery_interval=10):
        """device_factory(hive_dict) -> list of objects exposing run(stop_event)."""
        self.catalog = CatalogClient(catalog_url)
        self.factory = device_factory
        self.discovery_interval = discovery_interval
        self.running = {}  # hive_id -> (stop_event, [threads])
        self.log = logging.getLogger(name)
        self._stop = threading.Event()

    def sync_once(self):
        try:
            hives = self.catalog.get_hives()
        except CatalogError as e:
            self.log.warning("%s", e)
            return
        current = {h["hive_id"]: h for h in hives}

        for hive_id, hive in current.items():
            if hive_id not in self.running:
                self.log.info("New hive detected: %s -> starting devices", hive_id)
                stop = threading.Event()
                threads = []
                for device in self.factory(hive):
                    t = threading.Thread(target=device.run, args=(stop,), daemon=True,
                                         name=f"{hive_id}-{device.device_type}")
                    t.start()
                    threads.append(t)
                self.running[hive_id] = (stop, threads)

        for hive_id in list(self.running):
            if hive_id not in current:
                self.log.info("Hive %s removed from the Catalog -> stopping devices", hive_id)
                self.running.pop(hive_id)[0].set()

    def stop(self):
        self._stop.set()
        for stop, threads in self.running.values():
            stop.set()
        for _, threads in self.running.values():
            for t in threads:
                t.join(timeout=3)

    def run(self):
        self.log.info("Watching the Catalog for hives (every %ss)", self.discovery_interval)
        try:
            while not self._stop.is_set():
                self.sync_once()
                self._stop.wait(self.discovery_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.log.info("Stopped")
