# Apisense: Code Review and Refactoring Report

**Scope:** the full original Apisense code base (latest development version; the other development branches were also compared).
**Outcome:** the project could not run in its original state. It has been refactored into a working, tested system that keeps the original architecture: Catalog, MQTT devices, Alerts Engine, ThingSpeak and Telegram.

## 1. Summary

| | Before | After |
|---|---|---|
| Runs end to end | ❌ (import errors, mismatched keys, wrong ports) | ✅ verified with a full `run_all.py` session |
| Automated tests | none | 23 tests (REST, unit, end-to-end over MQTT), all passing |
| Telegram alerts reach the user | ❌ (nobody published on the bot's topic) | ✅ |
| Secrets in the source code | ❌ Telegram token and ThingSpeak key hardcoded | ✅ moved to a gitignored `secrets.json` |
| New hive while running | partly working, with duplicates | ✅ hot-plug via CLI, REST or Telegram |
| Documentation | README described files that didn't exist | README, ARCHITECTURE and this report |

## 2. Issues found in the original code

### 2.1 Blocking issues: the system could not start

| # | File | Problem |
|---|------|---------|
| B1 | `catalog.py` | Used **Flask**, which is not in `requirements.txt`. The README and the course both specify CherryPy, so `pip install -r requirements.txt` followed by `python catalog.py` failed. |
| B2 | `main_sensors.py` | Imported `Apisense_IoT_Project.sensors.EnvSensor`, a package that doesn't exist, so the script failed with `ModuleNotFoundError`. |
| B3 | `main_actuators.py` | Imported `Apisense_IoT_Project.actuators.actuator_vibr`, which doesn't exist (`ModuleNotFoundError`). |
| B4 | `main_sensors.py`, `main_actuators.py` | Read `hive["hive_id"]`, but the Catalog stored `hiveid` → `KeyError`. |
| B5 | `main_sensors.py` | Called `EnvSensor(f"ENV_{hid}", hid)` (2 arguments; the class takes 1) and `s.register(CATALOG_URL)` (the method takes none). Also read `s.sensor_type`, an attribute that doesn't exist. |
| B6 | `main_actuators.py` | POSTed to `/`, not `/register`, using the key `hive_id`. Also called `VibrationActuator(act_id, hid)` with the wrong signature. |
| B7 | `thingspeak.py` | `CATALOG_URL` pointed to port **5000**, but the Catalog runs on 8080. |
| B8 | `telegram_bot.py` | Opened `Catalog.json` relative to the *current working directory*, and read `h['hive_id']` when the key was `hiveid` → `FileNotFoundError` or `KeyError`. |

### 2.2 Functional bugs

| # | File | Problem | Effect |
|---|------|---------|--------|
| F1 | `telegram_bot.py` | Subscribed to `apisense/alerts/telegram`, but **no component published there**. | The beekeeper never received an alert. |
| F2 | `telegram_bot.py` | Built the actuator topic as `apisense/{hid}/{hid}_actu0`, while the Catalog assigned `apisense/{hid}/{hid}_a0`. | Manual ON/OFF commands never reached the actuator. |
| F3 | `telegram_bot.py` | `AUTHORIZED_USERS` was defined but **never checked**. | Any Telegram user could control the hives. |
| F4 | `catalog.py` | Every actuator registration **appended a new actuator**, even though hive creation had already provisioned `a0`. | `Hive_01` had collected a0, a1 and a2, one per restart. |
| F5 | `AlertsEngine.py` | Sent the command to the **last** actuator in the list (`actuators[-1]`). | Combined with F4, commands went to a "ghost" actuator that nothing listened to. |
| F6 | `AlertsEngine.py` | Published ON or OFF on **every** vibration reading. | A message flood, and a deterrent toggling every 2 s around the threshold. |
| F7 | `basesensor.py`, `baseactuator.py` | Read `broker` and `port` from the registration response, which never contained them. | The broker was silently hardcoded. |
| F8 | `VibrationSensor.py` | `uniform(0, 5)` against a 2.5 threshold. | About 50 % of readings were "intrusions", so the alert logic meant nothing. |
| F9 | `thingspeak.py` | Field mapping hardcoded for `Hive_01` and `Hive_02`. It detected weight with `"weight" in topic`, but topics contain sensor IDs such as `Hive_01_s1`, not the word "weight". | Weight was never uploaded, and new hives were ignored. |
| F10 | `thingspeak.py` | The rate limit was checked only when a message arrived, and on failure the buffer was cleared anyway. | Data was lost. |
| F11 | `myMQTT.py` | Fixed client IDs such as `AE_Main` or `TelegramBot_Group3` on a **public** broker, and no re-subscription after a reconnect. | Clients kicked each other off, and subscriptions were silently lost. |
| F12 | `catalog.py` | `FILE = 'Catalog.json'` was relative to the current working directory. | Running from the repository root created a second, different catalog. That is why a stray `Catalog.json` sat at the repository root. |
| F13 | `script_to_istantiate_hive/create_hive.py` | Wrote `Catalog.json` directly, behind the running server's back, using different keys (`hive_id`, `vibration_threshold`). | The catalog became inconsistent. |
| F14 | `catalog.py` | No locking, and plain non-atomic writes. | CherryPy and Flask serve requests concurrently, so the catalog could be corrupted. |

### 2.3 Security

| # | Problem | Fix |
|---|---------|-----|
| S1 | The Telegram bot token was hardcoded in `telegram_bot.py` and is present in the **git history**. | Moved to `secrets.json` (gitignored), with `secrets.example.json` as a template. **Recommendation:** revoke the token with @BotFather → `/revoke`, because it is in old commits. |
| S2 | The ThingSpeak write key was hardcoded in `thingspeak.py` and is also in the git history. | Moved to `secrets.json`. **Recommendation:** regenerate the key in the ThingSpeak channel settings. |
| S3 | Telegram authorisation was not enforced (F3). | Every command and button is now checked against `authorized_users`. |
| S4 | The Catalog accepted any string as a hive ID. IDs containing `/`, `+` or `#` corrupt MQTT topics. | Strict validation: `^[A-Za-z0-9_-]{1,32}$`. |

### 2.4 Repository hygiene

- `ForNowOptional/` held 8 files, all empty: the dashboard, the data simulations and `test_exam.py`. They were removed.
- These files were empty, obsolete or duplicates: `hive.py` (empty), `start_hive.py`, `hivecreation.py`, `script_to_istantiate_hive/`, and the root `Catalog.json`. They were removed or replaced by `hive_manager.py`.
- These notes were team- or tool-specific: `workflow.txt` (described a layout that was never built), `for_venv.md`, `run_instructions.md`, `GitHub_commands.txt` and `GitHub_instructions.md`. They were merged into the README or removed.
- Comments mixed Italian and English, and some contained leftover citation markers (`[cite: 72]`). Everything is now in English.

## 3. What was changed

The original architecture and OOP approach were kept and finished:

1. **Catalog rewritten in CherryPy** with a RESTful resource design: `/broker`, `/hives`, `/hives/<id>` and `/devices`, using GET, POST, PUT and DELETE.
   - The business logic (`CatalogStore`) is separate from the HTTP layer.
   - Writes are thread-safe and atomic, input is validated, errors come back as JSON with proper status codes, and registration is idempotent.
2. **Clear MQTT topic tree:** `…/sensors/<id>`, `…/actuators/<id>/cmd`, `…/actuators/<id>/status` (retained) and `…/<hive>/alerts`.
3. **SenML-style payloads** for all sensor data.
4. **Devices.**
   - `BaseSensor` and `BaseActuator` handle the full life cycle (register, connect, loop) with a stop event.
   - Actuators publish their real state.
   - The sensor simulations are stateful and realistic.
5. **`HiveDeviceManager`** replaces the broken `main_sensors.py` and `main_actuators.py` loops. Each device runs in its own thread, and devices start or stop automatically when hives are added to or removed from the Catalog.
6. **Alerts Engine.**
   - One state machine per hive, with hysteresis.
   - Commands and alerts are sent only on transitions.
   - Thresholds are reloaded live from the Catalog.
   - Alerts are published so the Telegram bot actually receives them.
7. **ThingSpeak adaptor.**
   - Configurable field mapping, with wildcard support and several channels.
   - A background flush that respects the rate limit, retries on failure, and a dry-run mode.
8. **Telegram bot.**
   - Authorisation is enforced, and alerts are forwarded.
   - New and changed commands: `/status` (live readings and states), `/on`, `/off`, `/threshold`, `/addhive`, plus inline buttons.
   - All topics come from the Catalog.
9. **Configuration.** `settings.json` holds the settings, `secrets.json` the secrets, and environment variables override both.
10. **Tooling.** `run_all.py` starts everything in the right order, and the `hive_manager.py` CLI manages hives.
11. **Tests and documentation.** A pytest suite, plus the README, `docs/ARCHITECTURE.md` and this report.

## 4. Verification

### 4.1 Automated tests

```
$ python -m pytest -q
.......................                                                  [100%]
23 passed in 8.21s
```

| Test file | What it checks |
|-----------|----------------|
| `test_catalog.py` (8) | Real HTTP calls to CherryPy: default provisioning and topic layout; 409 on duplicate hives; 400 on invalid IDs, thresholds and JSON; 404 on unknown resources; idempotent registration (no duplicate actuators); update and delete; persistence and bootstrap |
| `test_logic.py` (13) | Alerts Engine: threshold crossing, no flooding, hysteresis, live thresholds, new hives, malformed input. ThingSpeak: mapping, rate limit, retry, dry-run. Telegram: authorisation, actuation topics, `/threshold`, `/addhive`, `/hives`, `/status`, alert forwarding, chat persistence. Simulation plausibility (no false alarms on a calm hive). |
| `test_integration.py` (2) | **End to end over a real Mosquitto broker:** a sensor intrusion → the Alerts Engine → deterrent ON, with a retained status from `alerts_engine` → alert received by the bot logic → values reach ThingSpeak (`alert = 1`) → calm readings → OFF and "resolved" → a manual Telegram `/on` → `/status` shows the live state. Also hot-plugging a hive at runtime and stopping its devices after deletion. |

### 4.2 Manual system run

`run_all.py` was run with the local broker, a temporary catalog, ThingSpeak in dry-run and an intrusion probability of 15 %. During the run, `Hive_03` was added with `hive_manager.py create Hive_03 2.0`. The log showed:

```
alerts     | Running on localhost:1883, monitoring 2 hive(s): Hive_01 (thr 2.5 g), Hive_02 (thr 3.0 g)
actuators  | New hive detected: Hive_01 -> starting devices
sensors    | New hive detected: Hive_01 -> starting devices
alerts     | ALERT Hive_02: vibration 3.25 g >= threshold 3.00 g -> deterrent ON
actuators  | [deterrent-actuator:Hive_02] >>> Ultrasonic deterrent ACTIVATED <<<
thingspeak | [DRY-RUN] would upload: {... 'Hive_02/vibration': 4.18, 'Hive_02/alert': 1, ...}
actuators  | New hive detected: Hive_03 -> starting devices        <- hot-plug while running
sensors    | [vibration-sensor:Hive_03] Registered as Hive_03_s0 -> apisense/Hive_03/sensors/Hive_03_s0
alerts     | RESOLVED Hive_02 -> deterrent OFF
```

Further checks:

- The real Telegram bot started (`Bot @ApisenseBot online`) and the token was valid when tested.
- The public broker `broker.hivemq.com:1883` was reachable.

### 4.3 Not verified automatically

- **Real ThingSpeak uploads.** These would write test data into the group's channel. The HTTP request format follows the ThingSpeak `update.json` API, and everything up to the HTTP call is covered by the tests.
- **Interactive Telegram conversations.** These would message real users. The command logic is fully covered by unit tests, and the bot start-up was verified.

## 5. Recommendations

1. **Revoke the Telegram token and regenerate the ThingSpeak write key** (S1, S2). Both are in the git history of every branch. Alternatively, publish the repository with a fresh history.
2. For a demo on a shared or public network, change `mqtt.base_topic` to something unique, or use a private broker. Anyone can publish on `apisense/#` on HiveMQ's public broker.
3. Possible next steps:
   - honey-yield forecast from the weight trend, using ThingSpeak/MATLAB analysis or a small Python service;
   - swarm detection from a sudden weight drop;
   - a web dashboard that reads the Catalog and ThingSpeak.
