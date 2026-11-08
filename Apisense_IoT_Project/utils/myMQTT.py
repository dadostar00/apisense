"""
myMQTT.py - Thin wrapper around paho-mqtt (2.x) used by every component.

It follows the course pattern: the owner object (the "notifier") exposes a
``notify(topic, payload)`` method that is called for every received message.

Improvements over a bare paho client:
* the client ID is made unique with a random suffix - on a *public* broker two
  clients with the same ID kick each other off continuously;
* subscriptions are remembered and automatically restored after a reconnect;
* ``start()`` waits (briefly) for the connection so the first publish is not lost.
"""
import logging
import threading
import uuid

import paho.mqtt.client as mqtt

log = logging.getLogger("MQTT")


class MyMQTT:
    def __init__(self, client_id, broker, port, notifier=None, unique=True):
        self.broker = broker
        self.port = int(port)
        self.notifier = notifier
        self.client_id = f"{client_id}-{uuid.uuid4().hex[:6]}" if unique else client_id
        self._topics = set()
        self._connected = threading.Event()

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------------ callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("%s: connection refused (%s)", self.client_id, reason_code)
            return
        log.debug("%s connected to %s:%s", self.client_id, self.broker, self.port)
        # Restore subscriptions (needed after an automatic reconnect)
        for topic in self._topics:
            self._client.subscribe(topic, qos=1)
        self._connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()
        if reason_code != 0:
            log.warning("%s disconnected (%s), reconnecting...", self.client_id, reason_code)

    def _on_message(self, client, userdata, msg):
        if self.notifier is None:
            return
        try:
            self.notifier.notify(msg.topic, msg.payload.decode("utf-8"))
        except Exception:  # never let a bad message kill the network thread
            log.exception("Error while handling message on %s", msg.topic)

    # ------------------------------------------------------------------ API
    def start(self, timeout=10):
        """Connect and start the network loop. Returns True once connected."""
        self._client.connect_async(self.broker, self.port, keepalive=60)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            log.warning("%s: broker %s:%s not reachable yet, will keep retrying",
                        self.client_id, self.broker, self.port)
            return False
        return True

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()

    @property
    def is_connected(self):
        return self._connected.is_set()

    def myPublish(self, topic, msg, retain=False):
        info = self._client.publish(topic, msg, qos=1, retain=retain)
        log.debug("PUB %s %s", topic, msg[:80])
        return info

    def mySubscribe(self, topic):
        self._topics.add(topic)
        if self.is_connected:
            self._client.subscribe(topic, qos=1)
        log.debug("SUB %s", topic)

    def myUnsubscribe(self, topic):
        self._topics.discard(topic)
        self._client.unsubscribe(topic)
