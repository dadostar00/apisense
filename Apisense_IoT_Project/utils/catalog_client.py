"""
catalog_client.py - Small REST client used by every component to talk to the Catalog.

The Catalog is the single source of truth for: the MQTT broker address,
the base topic, the list of hives, their thresholds and the devices
(with their MQTT topics). No component hardcodes any of these.
"""
import time

import requests


class CatalogError(Exception):
    pass


class CatalogClient:
    def __init__(self, base_url, timeout=5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise CatalogError(f"Catalog not reachable at {self.base_url}: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text}
        if r.status_code >= 400:
            raise CatalogError(body.get("error", f"HTTP {r.status_code}"))
        return body

    # --- read ---------------------------------------------------------------
    def get_catalog(self):
        return self._request("GET", "/")

    def get_broker(self):
        """Return {"broker": ..., "port": ..., "base_topic": ...}."""
        return self._request("GET", "/broker")

    def get_hives(self):
        return self._request("GET", "/hives")

    def get_hive(self, hive_id):
        return self._request("GET", f"/hives/{hive_id}")

    # --- write --------------------------------------------------------------
    def create_hive(self, hive_id, vibration_threshold=None, location=None):
        body = {"hive_id": hive_id}
        if vibration_threshold is not None:
            body["vibration_threshold"] = vibration_threshold
        if location:
            body["location"] = location
        return self._request("POST", "/hives", json=body)

    def update_hive(self, hive_id, **fields):
        return self._request("PUT", f"/hives/{hive_id}", json=fields)

    def delete_hive(self, hive_id):
        return self._request("DELETE", f"/hives/{hive_id}")

    def register_device(self, hive_id, kind, device_type):
        """Register (or re-register) a sensor/actuator. Idempotent per (hive, type)."""
        return self._request("POST", "/devices",
                             json={"hive_id": hive_id, "kind": kind, "device_type": device_type})

    # --- helpers ------------------------------------------------------------
    def wait_until_ready(self, retries=30, delay=1.0):
        """Block until the Catalog answers (used at start-up)."""
        last = None
        for _ in range(retries):
            try:
                return self.get_broker()
            except CatalogError as e:
                last = e
                time.sleep(delay)
        raise CatalogError(str(last))
