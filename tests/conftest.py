"""Shared pytest fixtures for the Apisense test-suite."""
import socket
import sys
import uuid
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent / "Apisense_IoT_Project"
sys.path.insert(0, str(PROJECT))

import cherrypy  # noqa: E402

import catalog as catalog_module  # noqa: E402
from utils.catalog_client import CatalogClient  # noqa: E402

TEST_BROKER = "localhost"
TEST_BROKER_PORT = 1883


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def broker_available():
    try:
        with socket.create_connection((TEST_BROKER, TEST_BROKER_PORT), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def base_topic():
    # Unique base topic so parallel/previous runs never interfere
    return f"apisense_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def catalog_server(tmp_path_factory, base_topic):
    """A real CherryPy Catalog running in-process on a random port."""
    path = tmp_path_factory.mktemp("catalog") / "Catalog.json"
    store = catalog_module.CatalogStore(path, TEST_BROKER, TEST_BROKER_PORT, base_topic, 2.5)
    port = _free_port()
    catalog_module.mount(store)
    cherrypy.config.update({"server.socket_host": "127.0.0.1", "server.socket_port": port,
                            "log.screen": False, "engine.autoreload.on": False,
                            "checker.on": False})
    cherrypy.engine.start()
    cherrypy.engine.wait(cherrypy.engine.states.STARTED)
    url = f"http://127.0.0.1:{port}"
    CatalogClient(url).wait_until_ready(retries=20, delay=0.2)
    yield url, store
    cherrypy.engine.exit()


@pytest.fixture
def catalog_url(catalog_server):
    return catalog_server[0]
