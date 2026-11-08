"""
hive_manager.py - Command-line tool to manage hives through the Catalog REST API.
Works while the whole system is running: new hives are picked up automatically
by the sensor/actuator managers, the Alerts Engine and the Telegram bot.

Usage:
  python hive_manager.py list
  python hive_manager.py show <hive_id>
  python hive_manager.py create <hive_id> [threshold] [location]
  python hive_manager.py threshold <hive_id> <value>
  python hive_manager.py delete <hive_id>
"""
import argparse
import json
import sys

from utils.catalog_client import CatalogClient, CatalogError
from utils.config import load_settings


def print_hive(h):
    print(f"{h['hive_id']:<12} threshold={h['vibration_threshold']} g"
          + (f"  location={h['location']}" if h.get("location") else ""))
    for d in h.get("sensors", []) + h.get("actuators", []):
        print(f"    {d['id']:<14} {d['kind']:<8} {d['type']:<12} {d['topic']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Manage Apisense hives via the Catalog")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("show").add_argument("hive_id")
    c = sub.add_parser("create")
    c.add_argument("hive_id")
    c.add_argument("threshold", nargs="?", type=float)
    c.add_argument("location", nargs="?")
    t = sub.add_parser("threshold")
    t.add_argument("hive_id")
    t.add_argument("value", type=float)
    sub.add_parser("delete").add_argument("hive_id")
    args = p.parse_args(argv)

    catalog = CatalogClient(load_settings()["catalog"]["url"])
    try:
        if args.cmd == "list":
            hives = catalog.get_hives()
            if not hives:
                print("No hives registered.")
            for h in hives:
                print_hive(h)
        elif args.cmd == "show":
            print(json.dumps(catalog.get_hive(args.hive_id), indent=2))
        elif args.cmd == "create":
            print("Created:")
            print_hive(catalog.create_hive(args.hive_id, args.threshold, args.location))
        elif args.cmd == "threshold":
            h = catalog.update_hive(args.hive_id, vibration_threshold=args.value)
            print(f"{h['hive_id']}: threshold set to {h['vibration_threshold']} g")
        elif args.cmd == "delete":
            catalog.delete_hive(args.hive_id)
            print(f"{args.hive_id} deleted")
    except CatalogError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
