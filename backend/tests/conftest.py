import os

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://ii:ii_dev_password@localhost:5432/ii_test")
os.environ["ANTHROPIC_API_KEY"] = ""  # deterministic agent in tests unless a test overrides
os.environ.pop("TIINGO_API_KEY", None)
os.environ.pop("ALPHAVANTAGE_API_KEY", None)
os.environ.pop("FRED_API_KEY", None)
os.environ.pop("SEC_USER_AGENT", None)
os.environ["ENABLE_FRANKFURTER"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
s = get_settings()
s.anthropic_api_key = None


def _reset_schema(url: str) -> None:
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()


@pytest.fixture(scope="session")
def migrated():
    url = os.environ["DATABASE_URL"]
    _reset_schema(url)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    from app.db import reset_engine

    reset_engine(url)
    return url


@pytest.fixture(scope="session")
def sample_loaded(migrated):
    from app.db import session_scope
    from app.services.bootstrap import ensure_sample_data

    with session_scope() as db:
        ensure_sample_data(db)
    return True


@pytest.fixture(scope="session")
def app_client(sample_loaded):
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


def _register(client, email, password="Str0ngPassw0rd!", with_demo=True):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "with_demo": with_demo})
    assert r.status_code == 200, r.text
    client.headers.update({"X-Requested-With": "ii"})
    return r.json()


@pytest.fixture()
def client_factory(sample_loaded):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    made = []

    def make(email: str, with_demo: bool = True):
        c = TestClient(app)
        u = _register(c, email, with_demo=with_demo)
        made.append(c)
        return c, u

    return make
