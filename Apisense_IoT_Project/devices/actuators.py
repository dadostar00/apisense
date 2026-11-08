"""
actuators.py - Simulated beehive actuators.

* DeterrentActuator - ultrasonic / buzzer anti-intrusion device mounted on the
  hive. Switched ON by the Alerts Engine when an intrusion is detected (or
  manually from the Telegram bot) and OFF when the hive is calm again.
"""
from utils.baseactuator import BaseActuator


class DeterrentActuator(BaseActuator):
    def __init__(self, hive_id, catalog_url):
        super().__init__("deterrent", hive_id, catalog_url)

    def actuate(self, state):
        # On real hardware this would drive a GPIO pin / relay.
        if state:
            self.log.warning(">>> Ultrasonic deterrent ACTIVATED <<<")
        else:
            self.log.info("Ultrasonic deterrent deactivated")


ACTUATOR_CLASSES = {"deterrent": DeterrentActuator}
