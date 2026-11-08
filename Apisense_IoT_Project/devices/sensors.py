"""
sensors.py - Simulated beehive sensors.

Each sensor keeps an internal state so that readings evolve realistically
(random walk around a plausible value) instead of being independent random numbers.

* VibrationSensor  - accelerometer on the hive body (g). Low background noise;
                     rare "intrusion" events (predator, e.g. hornet/bear/thief)
                     produce a burst of high readings for a few samples.
* WeightSensor     - load cell under the hive (kg). Slow growth during the
                     nectar flow + small noise.
* EnvSensor        - temperature (Cel) and relative humidity (%RH) inside the
                     hive; bees regulate the brood nest around 34-35 degrees C.
"""
import random

from utils.basesensor import BaseSensor


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


class VibrationSensor(BaseSensor):
    def __init__(self, hive_id, catalog_url, interval=5, intrusion_probability=0.03):
        super().__init__("vibration", hive_id, catalog_url, interval)
        self.intrusion_probability = intrusion_probability
        self._burst_left = 0

    def sense(self):
        if self._burst_left == 0 and random.random() < self.intrusion_probability:
            self._burst_left = random.randint(3, 6)          # an intrusion starts
        if self._burst_left > 0:
            self._burst_left -= 1
            value = random.uniform(3.2, 5.0)                 # strong shaking
        else:
            value = abs(random.gauss(0.6, 0.3))              # normal colony activity
        return [("vibration", "g", round(value, 2))]


class WeightSensor(BaseSensor):
    def __init__(self, hive_id, catalog_url, interval=15):
        super().__init__("weight", hive_id, catalog_url, interval)
        self._weight = random.uniform(28.0, 34.0)

    def sense(self):
        self._weight = _clamp(self._weight + random.gauss(0.01, 0.05), 15.0, 80.0)
        return [("weight", "kg", round(self._weight, 2))]


class EnvSensor(BaseSensor):
    def __init__(self, hive_id, catalog_url, interval=15):
        super().__init__("environment", hive_id, catalog_url, interval)
        self._temp = random.uniform(33.5, 35.0)
        self._hum = random.uniform(55.0, 65.0)

    def sense(self):
        # Mean-reverting random walk (bees keep the nest stable)
        self._temp = _clamp(self._temp + 0.2 * (34.5 - self._temp) + random.gauss(0, 0.15), 10, 45)
        self._hum = _clamp(self._hum + 0.2 * (60.0 - self._hum) + random.gauss(0, 1.0), 20, 95)
        return [("temperature", "Cel", round(self._temp, 2)), ("humidity", "%RH", round(self._hum, 2))]


SENSOR_CLASSES = {"vibration": VibrationSensor, "weight": WeightSensor, "environment": EnvSensor}


if __name__ == "__main__":
    # Run a single sensor stand-alone:  python -m devices.sensors <type> <hive_id>
    import sys
    from utils.config import load_settings, setup_logging

    setup_logging("sensor")
    s = load_settings()
    sensor_type = sys.argv[1] if len(sys.argv) > 1 else "vibration"
    hive = sys.argv[2] if len(sys.argv) > 2 else "Hive_01"
    SENSOR_CLASSES[sensor_type](hive, s["catalog"]["url"]).run()
