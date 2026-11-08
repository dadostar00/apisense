"""
run_all.py - Starts the whole Apisense system with one command, in the right order:

  1. catalog.py              (REST registry, everything depends on it)
  2. alerts_engine.py        (subscribers first, so no data is missed)
     thingspeak_adaptor.py
     telegram_bot.py         (skipped if no Telegram token is configured)
  3. main_actuators.py
  4. main_sensors.py         (data starts flowing)

Output of every component is printed with its name as prefix.
Press Ctrl+C to stop everything.

Usage:  python run_all.py [--no-telegram] [--no-thingspeak]
"""
import argparse
import os
import subprocess
import sys
import threading
import time

from utils.catalog_client import CatalogClient, CatalogError
from utils.config import BASE_DIR, load_secrets, load_settings


def stream(proc, name):
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(f"{name:<11}| {line}")
        sys.stdout.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-telegram", action="store_true")
    p.add_argument("--no-thingspeak", action="store_true")
    args = p.parse_args()

    settings, secrets = load_settings(), load_secrets()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    procs = []

    def launch(name, script):
        proc = subprocess.Popen([sys.executable, str(BASE_DIR / script)], cwd=BASE_DIR, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        threading.Thread(target=stream, args=(proc, name), daemon=True).start()
        procs.append((name, proc))

    try:
        launch("catalog", "catalog.py")
        try:
            CatalogClient(settings["catalog"]["url"]).wait_until_ready(retries=20, delay=0.5)
        except CatalogError as e:
            raise SystemExit(f"Catalog did not start: {e}")

        launch("alerts", "alerts_engine.py")
        if not args.no_thingspeak:
            launch("thingspeak", "thingspeak_adaptor.py")
        if args.no_telegram:
            pass
        elif secrets["telegram"]["token"]:
            launch("telegram", "telegram_bot.py")
        else:
            print("run_all    | Telegram token not configured -> telegram_bot.py not started")
        time.sleep(2)
        launch("actuators", "main_actuators.py")
        time.sleep(1)
        launch("sensors", "main_sensors.py")

        while True:
            for name, proc in procs:
                if proc.poll() is not None:
                    raise SystemExit(f"{name} exited with code {proc.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nrun_all    | stopping...")
    finally:
        for name, proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
        for name, proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
