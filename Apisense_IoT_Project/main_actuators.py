"""
main_actuators.py - Starts the simulated deterrent actuator of every hive
registered in the Catalog, and keeps watching for new hives.

Run:  python main_actuators.py
"""
from devices.actuators import DeterrentActuator
from utils.config import load_settings, setup_logging
from utils.device_manager import HiveDeviceManager


def main():
    setup_logging("ActuatorManager")
    s = load_settings()
    url = s["catalog"]["url"]

    def factory(hive):
        return [DeterrentActuator(hive["hive_id"], url)]

    HiveDeviceManager("ActuatorManager", url, factory, s["managers"]["discovery_interval"]).run()


if __name__ == "__main__":
    main()
