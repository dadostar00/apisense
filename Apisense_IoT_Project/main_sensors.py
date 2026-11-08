"""
main_sensors.py - Starts the simulated sensors (vibration, weight, environment)
of every hive registered in the Catalog, and keeps watching for new hives.

Run:  python main_sensors.py
"""
from devices.sensors import EnvSensor, VibrationSensor, WeightSensor
from utils.config import load_settings, setup_logging
from utils.device_manager import HiveDeviceManager


def main():
    setup_logging("SensorManager")
    s = load_settings()
    url, cfg = s["catalog"]["url"], s["sensors"]

    def factory(hive):
        hid = hive["hive_id"]
        return [
            VibrationSensor(hid, url, cfg["vibration_interval"], cfg["intrusion_probability"]),
            WeightSensor(hid, url, cfg["weight_interval"]),
            EnvSensor(hid, url, cfg["environment_interval"]),
        ]

    HiveDeviceManager("SensorManager", url, factory, s["managers"]["discovery_interval"]).run()


if __name__ == "__main__":
    main()
