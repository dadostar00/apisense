"""REST API tests of the Catalog (real HTTP requests against CherryPy)."""
import json

import requests

import catalog as catalog_module


def test_broker_info(catalog_url, base_topic):
    r = requests.get(f"{catalog_url}/broker")
    assert r.status_code == 200
    assert r.json() == {"broker": "localhost", "port": 1883, "base_topic": base_topic}


def test_create_hive_provisions_default_devices(catalog_url, base_topic):
    r = requests.post(f"{catalog_url}/hives", json={"hive_id": "T_create", "vibration_threshold": 3})
    assert r.status_code == 201
    hive = r.json()
    assert hive["vibration_threshold"] == 3.0
    assert [s["type"] for s in hive["sensors"]] == ["vibration", "weight", "environment"]
    assert [a["type"] for a in hive["actuators"]] == ["deterrent"]
    assert hive["sensors"][0]["topic"] == f"{base_topic}/T_create/sensors/T_create_s0"
    assert hive["actuators"][0]["topic"] == f"{base_topic}/T_create/actuators/T_create_a0/cmd"
    assert hive["alerts_topic"] == f"{base_topic}/T_create/alerts"
    # appears in the list and in the full catalog
    assert "T_create" in [h["hive_id"] for h in requests.get(f"{catalog_url}/hives").json()]
    assert "T_create" in [h["hive_id"] for h in requests.get(f"{catalog_url}/").json()["hives"]]


def test_duplicate_hive_is_rejected(catalog_url):
    requests.post(f"{catalog_url}/hives", json={"hive_id": "T_dup"})
    r = requests.post(f"{catalog_url}/hives", json={"hive_id": "T_dup"})
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]


def test_invalid_input(catalog_url):
    assert requests.post(f"{catalog_url}/hives", json={"hive_id": "bad/id"}).status_code == 400
    assert requests.post(f"{catalog_url}/hives", json={"hive_id": "x", "vibration_threshold": "a"}).status_code == 400
    assert requests.post(f"{catalog_url}/hives", data="not json").status_code == 400
    assert requests.get(f"{catalog_url}/nope").status_code == 404
    assert requests.get(f"{catalog_url}/hives/missing").status_code == 404


def test_device_registration_is_idempotent(catalog_url):
    requests.post(f"{catalog_url}/hives", json={"hive_id": "T_reg"})
    body = {"hive_id": "T_reg", "kind": "actuator", "device_type": "deterrent"}
    first = requests.post(f"{catalog_url}/devices", json=body)
    second = requests.post(f"{catalog_url}/devices", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == "T_reg_a0"
    assert first.json()["broker"] == "localhost"          # broker info is returned
    assert "status_topic" in first.json()
    hive = requests.get(f"{catalog_url}/hives/T_reg").json()
    assert len(hive["actuators"]) == 1                    # no duplicates (old bug)


def test_register_on_unknown_hive_or_type(catalog_url):
    r = requests.post(f"{catalog_url}/devices", json={"hive_id": "ghost", "kind": "sensor", "device_type": "weight"})
    assert r.status_code == 404
    requests.post(f"{catalog_url}/hives", json={"hive_id": "T_type"})
    r = requests.post(f"{catalog_url}/devices", json={"hive_id": "T_type", "kind": "sensor", "device_type": "laser"})
    assert r.status_code == 400


def test_update_and_delete(catalog_url):
    requests.post(f"{catalog_url}/hives", json={"hive_id": "T_upd"})
    r = requests.put(f"{catalog_url}/hives/T_upd", json={"vibration_threshold": 4.2, "location": "Lab"})
    assert r.status_code == 200 and r.json()["vibration_threshold"] == 4.2 and r.json()["location"] == "Lab"
    assert requests.put(f"{catalog_url}/hives/T_upd", json={"hive_id": "hack"}).status_code == 400
    assert requests.delete(f"{catalog_url}/hives/T_upd").status_code == 200
    assert requests.get(f"{catalog_url}/hives/T_upd").status_code == 404


def test_persistence_and_bootstrap(tmp_path):
    path = tmp_path / "Catalog.json"
    store = catalog_module.CatalogStore(path, "b", 1883, "base", 2.5,
                                        bootstrap_hives=[{"hive_id": "Seed", "vibration_threshold": 2.0}])
    assert [h["hive_id"] for h in store.list_hives()] == ["Seed"]
    store.create_hive("Other")
    on_disk = json.loads(path.read_text())
    assert [h["hive_id"] for h in on_disk["hives"]] == ["Seed", "Other"]
    # Re-opening loads the file and does NOT re-apply the bootstrap list
    store2 = catalog_module.CatalogStore(path, "b", 1883, "newbase", 2.5,
                                         bootstrap_hives=[{"hive_id": "Seed2"}])
    hives = store2.list_hives()
    assert [h["hive_id"] for h in hives] == ["Seed", "Other"]
    assert hives[0]["sensors"][0]["topic"].startswith("newbase/")   # topics follow settings
