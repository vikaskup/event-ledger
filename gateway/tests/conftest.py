import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["GATEWAY_DB_PATH"] = path

    from importlib import reload
    import app.db as db_module
    reload(db_module)
    import app.circuit_breaker as cb_module
    reload(cb_module)
    import app.account_client as client_module
    reload(client_module)
    import app.main as main_module
    reload(main_module)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        yield c

    os.remove(path)
