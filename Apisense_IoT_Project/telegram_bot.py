"""
telegram_bot.py - Telegram interface of Apisense (notifications + remote control).

Features
* Push notifications: every alert / "resolved" message published by the Alerts
  Engine on <base>/+/alerts is forwarded to the subscribed chats.
* Commands (only for authorized users, see secrets.json):
    /start                    subscribe this chat to alerts
    /stop                     unsubscribe this chat
    /help                     list of commands
    /hives                    list hives + inline buttons (ON / OFF / status)
    /status [hive_id]         latest readings, alert state and deterrent state
    /on <hive_id>             switch the deterrent ON manually  (alias /actuate)
    /off <hive_id>            switch the deterrent OFF manually (alias /deactivate)
    /threshold <hive> <g>     change the vibration threshold (PUT on the Catalog)
    /addhive <hive_id> [g]    register a new hive (POST on the Catalog)
* Everything (hives, actuator topics, thresholds) comes from the Catalog REST
  API; the latest readings come from MQTT. Nothing is hardcoded.

Run:  python telegram_bot.py      (requires the bot token in secrets.json)
"""
import json
import logging
import threading
import time
from pathlib import Path

from utils import senml
from utils.catalog_client import CatalogClient, CatalogError
from utils.config import BASE_DIR, load_secrets, load_settings, setup_logging
from utils.myMQTT import MyMQTT

log = logging.getLogger("TelegramBot")
CHATS_FILE = BASE_DIR / "bot_chats.json"

HELP = (
    "Apisense - smart beehive bot\n\n"
    "/hives - list hives (with control buttons)\n"
    "/status [hive_id] - latest readings and states\n"
    "/on <hive_id> - switch the deterrent ON\n"
    "/off <hive_id> - switch the deterrent OFF\n"
    "/threshold <hive_id> <g> - set the vibration threshold\n"
    "/addhive <hive_id> [threshold] - register a new hive\n"
    "/start - receive alerts in this chat\n"
    "/stop - stop receiving alerts\n"
    "/help - this message"
)


class BotLogic:
    """Command handling, independent from the Telegram library (unit-testable).

    Each handler returns (text, buttons) where buttons is a list of rows of
    (label, callback_data) tuples or None.
    """

    def __init__(self, catalog, publisher, authorized_users, alert_chat_ids=(), chats_file=None):
        self.catalog = catalog
        self.publish = publisher                       # publish(topic, payload_str)
        self.authorized = {str(u) for u in authorized_users}
        self.chats_file = Path(chats_file) if chats_file else None
        self.chats = set(str(c) for c in alert_chat_ids) | self._load_chats()
        self.latest = {}       # hive_id -> {quantity: (value, unit, timestamp)}
        self.actuators = {}    # hive_id -> {"state", "source", "timestamp"}
        self.alerts = {}       # hive_id -> last alert dict
        self._lock = threading.Lock()

    # ------------------------------------------------------------ persistence
    def _load_chats(self):
        if self.chats_file and self.chats_file.exists():
            try:
                return set(json.loads(self.chats_file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return set()

    def _save_chats(self):
        if self.chats_file:
            self.chats_file.write_text(json.dumps(sorted(self.chats)), encoding="utf-8")

    # ------------------------------------------------------------ auth
    def is_authorized(self, user_id):
        return not self.authorized or str(user_id) in self.authorized

    # ------------------------------------------------------------ MQTT side
    def on_mqtt(self, topic, payload):
        """Update caches. Returns an alert text to broadcast, or None."""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        hive_id = data.get("hive_id")
        if not hive_id:
            return None
        with self._lock:
            if topic.endswith("/alerts"):
                self.alerts[hive_id] = data
                icon = "\U0001F6A8" if data.get("level") == "alert" else "✅"
                return f"{icon} {data.get('msg', data)}"
            if topic.endswith("/status"):
                self.actuators[hive_id] = data
                return None
            try:
                header, _ = senml.parse(data)
            except ValueError:
                return None
            store = self.latest.setdefault(hive_id, {})
            for e in header["e"]:
                store[e["n"]] = (e["v"], e.get("u", ""), e.get("t", time.time()))
        return None

    # ------------------------------------------------------------ helpers
    def _hive_ids(self):
        return [h["hive_id"] for h in self.catalog.get_hives()]

    def _status_text(self, hive):
        hid = hive["hive_id"]
        with self._lock:
            readings = dict(self.latest.get(hid, {}))
            act = self.actuators.get(hid)
            alert = self.alerts.get(hid)
        lines = [f"\U0001F41D {hid}" + (f" ({hive['location']})" if hive.get("location") else ""),
                 f"Vibration threshold: {hive['vibration_threshold']} g"]
        if readings:
            for name, (v, u, t) in sorted(readings.items()):
                lines.append(f"  {name}: {v} {u}  ({int(time.time() - t)} s ago)")
        else:
            lines.append("  no readings received yet")
        lines.append("Deterrent: " + (f"{act['state']} (by {act.get('source', '?')})" if act else "unknown"))
        lines.append("Alert: " + ("ACTIVE" if alert and alert.get("level") == "alert" else "none"))
        return "\n".join(lines)

    def _command(self, hive_id, command):
        hive = self.catalog.get_hive(hive_id)  # raises CatalogError if missing
        topics = [a["topic"] for a in hive.get("actuators", []) if a.get("type") == "deterrent"]
        if not topics:
            return f"{hive_id} has no deterrent actuator."
        for t in topics:
            self.publish(t, json.dumps({"command": command, "source": "telegram",
                                        "reason": "manual command", "timestamp": time.time()}))
        return f"Command {command} sent to the deterrent of {hive_id}."

    # ------------------------------------------------------------ commands
    def handle(self, user_id, chat_id, text):
        """Entry point for a text command. Returns (reply_text, buttons)."""
        if not self.is_authorized(user_id):
            return f"Sorry, you are not authorized to use this bot (your user ID is {user_id}).", None
        parts = text.strip().split()
        if not parts:
            return HELP, None
        cmd, args = parts[0].split("@")[0].lower(), parts[1:]
        try:
            if cmd == "/start":
                self.chats.add(str(chat_id))
                self._save_chats()
                return "Apisense bot active: this chat will receive hive alerts.\n\n" + HELP, None
            if cmd == "/stop":
                self.chats.discard(str(chat_id))
                self._save_chats()
                return "This chat will no longer receive alerts.", None
            if cmd == "/help":
                return HELP, None
            if cmd == "/hives":
                hives = self.catalog.get_hives()
                if not hives:
                    return "No hives registered. Use /addhive <hive_id>.", None
                text = "Registered hives:\n" + "\n".join(
                    f"- {h['hive_id']} (threshold {h['vibration_threshold']} g)" for h in hives)
                buttons = [[("ON " + h["hive_id"], f"on:{h['hive_id']}"),
                            ("OFF " + h["hive_id"], f"off:{h['hive_id']}"),
                            ("Status", f"status:{h['hive_id']}")] for h in hives]
                return text, buttons
            if cmd == "/status":
                hives = [self.catalog.get_hive(args[0])] if args else self.catalog.get_hives()
                return ("\n\n".join(self._status_text(h) for h in hives) or "No hives registered."), None
            if cmd in ("/on", "/actuate", "/off", "/deactivate"):
                if not args:
                    return f"Usage: {cmd} <hive_id>", None
                return self._command(args[0], "ON" if cmd in ("/on", "/actuate") else "OFF"), None
            if cmd == "/threshold":
                if len(args) != 2:
                    return "Usage: /threshold <hive_id> <value_in_g>", None
                hive = self.catalog.update_hive(args[0], vibration_threshold=float(args[1]))
                return f"Threshold of {hive['hive_id']} set to {hive['vibration_threshold']} g.", None
            if cmd == "/addhive":
                if not args:
                    return "Usage: /addhive <hive_id> [threshold]", None
                thr = float(args[1]) if len(args) > 1 else None
                hive = self.catalog.create_hive(args[0], thr)
                return (f"Hive {hive['hive_id']} created (threshold {hive['vibration_threshold']} g). "
                        "Its devices will start automatically within a few seconds."), None
        except CatalogError as e:
            return f"Error: {e}", None
        except ValueError:
            return "Error: the value must be a number.", None
        return "Unknown command.\n\n" + HELP, None

    def handle_button(self, user_id, data):
        if not self.is_authorized(user_id):
            return "Not authorized."
        action, _, hive_id = data.partition(":")
        try:
            if action in ("on", "off"):
                return self._command(hive_id, action.upper())
            if action == "status":
                return self._status_text(self.catalog.get_hive(hive_id))
        except CatalogError as e:
            return f"Error: {e}"
        return "Unknown action."


class HiveBot:
    """Glue between Telegram (pyTelegramBotAPI), MQTT and BotLogic."""

    def __init__(self, token, catalog_url, authorized_users, alert_chat_ids):
        import telebot  # imported here so BotLogic can be tested without it
        from telebot import types
        self.types = types
        self.bot = telebot.TeleBot(token, parse_mode=None)
        self.catalog = CatalogClient(catalog_url)
        self.client = None
        self.logic = BotLogic(self.catalog, self._publish, authorized_users, alert_chat_ids, CHATS_FILE)
        self._register_handlers()

    def _publish(self, topic, payload):
        self.client.myPublish(topic, payload)

    def _markup(self, buttons):
        if not buttons:
            return None
        markup = self.types.InlineKeyboardMarkup()
        for row in buttons:
            markup.row(*[self.types.InlineKeyboardButton(label, callback_data=data) for label, data in row])
        return markup

    def _register_handlers(self):
        @self.bot.message_handler(func=lambda m: bool(m.text) and m.text.startswith("/"))
        def on_command(message):
            text, buttons = self.logic.handle(message.from_user.id, message.chat.id, message.text)
            self.bot.send_message(message.chat.id, text, reply_markup=self._markup(buttons))

        @self.bot.callback_query_handler(func=lambda call: True)
        def on_button(call):
            reply = self.logic.handle_button(call.from_user.id, call.data)
            self.bot.answer_callback_query(call.id, reply[:190])
            self.bot.send_message(call.message.chat.id, reply)

    # MQTT callback
    def notify(self, topic, payload):
        alert_text = self.logic.on_mqtt(topic, payload)
        if alert_text:
            for chat_id in list(self.logic.chats):
                try:
                    self.bot.send_message(chat_id, alert_text)
                except Exception as e:
                    log.warning("Cannot notify chat %s: %s", chat_id, e)
            log.info("Alert forwarded to %d chat(s): %s", len(self.logic.chats), alert_text)

    def run(self):
        broker = self.catalog.wait_until_ready()
        base = broker["base_topic"]
        self.client = MyMQTT("TelegramBot", broker["broker"], broker["port"], notifier=self)
        for t in (f"{base}/+/alerts", f"{base}/+/sensors/+", f"{base}/+/actuators/+/status"):
            self.client.mySubscribe(t)
        self.client.start()
        me = self.bot.get_me()
        log.info("Bot @%s online. Alert chats: %s", me.username, sorted(self.logic.chats) or "none (send /start)")
        if not self.logic.authorized:
            log.warning("authorized_users is empty: ANY Telegram user can control the hives!")
        try:
            self.bot.infinity_polling(timeout=20, long_polling_timeout=20)
        finally:
            self.client.stop()


def main():
    setup_logging("TelegramBot")
    s, secrets = load_settings(), load_secrets()
    tg = secrets["telegram"]
    if not tg["token"]:
        raise SystemExit("Telegram token missing: copy secrets.example.json to secrets.json "
                         "and set telegram.token (or set the TELEGRAM_TOKEN env variable).")
    HiveBot(tg["token"], s["catalog"]["url"], tg["authorized_users"], tg["alert_chat_ids"]).run()


if __name__ == "__main__":
    main()
