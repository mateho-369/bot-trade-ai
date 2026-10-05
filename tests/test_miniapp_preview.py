"""Public synthetic fixtures are NOT owner auth and exist only in preview mode."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_services import OwnerServices
from core.database import Database
from core.models import AuditLog, OwnerApproval
from core.settings import Settings
from miniapp.preview_data import preview_data
from tests.owner_helpers import api_client, owner_services
from tests.risk_helpers import MOMENT
from trading.types import ManualClock


@pytest.fixture
def services(tmp_path):
    cfg = Settings(
        _env_file=None, project_root=tmp_path, api_trusted_hosts=("owner.example", "localhost", "*.e2b.app")
    )
    db = Database(cfg)
    db.initialize()
    return OwnerServices(db, cfg, clock=ManualClock(MOMENT), preview_only=True)


async def test_preview_public_dataset_no_auth_or_trading_capability(services):
    async with api_client(services, preview=True) as (client, _):
        response = await client.get("/preview/data")
        assert response.status_code == 200
        data = response.json()
        assert data["preview_only"] and data["dashboard"]["meta"]["fixture_only"]
        assert not any(data["dashboard"]["capabilities"].values())
        assert (await client.get("/api/dashboard")).status_code == 401
        assert "frame-ancestors *" in response.headers["content-security-policy"]
    assert services.execution is None and services.settings.telegram_owner_id is None


@pytest.mark.parametrize(
    "action",
    [
        "pause",
        "resume",
        "kill",
        "close_position",
        "close_all",
        "approve_suggestion",
        "reject_suggestion",
        "cancel_confirmation",
        "ai_fallback",
        "ai_reset",
        "alerts/ack",
    ],
)
async def test_every_preview_mutation_denied_without_writes(services, action):
    async with api_client(services, preview=True) as (client, _):
        response = await client.post("/api/" + action, json={"request_id": str(uuid4())})
    assert (
        response.status_code == 403 and response.json()["error"]["code"] == "synthetic_preview_is_read_only"
    )
    with services.database.session() as sql:
        assert not sql.scalar(select(OwnerApproval.id))
        assert len(sql.scalars(select(AuditLog)).all()) == 1


async def test_assets_allowlist_no_source_env_or_traversal(services):
    async with api_client(services, preview=True) as (client, _):
        page = await client.get("/")
        assert 'data-preview="true"' in page.text and "__PREVIEW__" not in page.text
        for file in ["styles.css", "app.js", "telegram_loader.js"]:
            assert (await client.get("/static/" + file)).status_code == 200
        for file in [".env", "server.py", "../core/settings.py", "nonexistent.js"]:
            assert (await client.get("/static/" + file)).status_code == 404


async def test_production_never_mounts_preview_data(tmp_path):
    actual = owner_services(tmp_path)
    async with api_client(actual) as (client, _):
        assert (await client.get("/preview/data")).status_code == 404
        assert 'data-preview="false"' in (await client.get("/")).text


async def test_preview_factory_mismatch_or_owner_creds_cannot_silently_enable(tmp_path):
    real = owner_services(tmp_path)
    from miniapp.server import create_app

    with pytest.raises(ValueError):
        create_app(real.settings, real, preview_only=True)
    with pytest.raises(ValueError):
        OwnerServices(real.database, real.settings, preview_only=True)


def test_preview_data_returns_fresh_copy_no_secret_state():
    first = preview_data()
    first["dashboard"]["kpis"]["balance"] = "999999"
    assert preview_data()["dashboard"]["kpis"]["balance"] == "1000.00"
    assert "telegram_bot_token" not in str(preview_data())
    assert "confirmation_token" not in str(preview_data())


def test_preview_launcher_ignores_dotenv_and_inherited_live_credentials(monkeypatch):
    import scripts.preview_owner_ui as launcher

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:TEST_ONLY_ENV_TOKEN")
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "42")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("PAPER_TRADING", "false")
    seen = {}

    def captured(application, **kwargs):
        services = application.state.owner_services
        seen.update(
            owner=services.settings.telegram_owner_id,
            token=services.settings.telegram_bot_token.get_secret_value(),
            mode=services.settings.mode.value,
            execution=services.execution,
            host=kwargs["host"],
        )

    monkeypatch.setattr(launcher.uvicorn, "run", captured)
    assert launcher.main(["--host", "0.0.0.0", "--port", "8000"]) == 0
    assert seen == {"owner": None, "token": "", "mode": "paper", "execution": None, "host": "0.0.0.0"}


def test_frontend_never_trusts_unsafe_ids_persists_auth_or_builds_html():
    root = Path(__file__).resolve().parents[1] / "miniapp" / "static"
    script = (root / "app.js").read_text()
    for forbidden in [
        ".innerHTML",
        "localStorage.",
        "sessionStorage.",
        "document.cookie",
        "initDataUnsafe.user",
        "127.0.0.1",
        "localhost",
        "Authorization:",
    ]:
        assert forbidden not in script
    assert '"X-Telegram-Init-Data": signedInitData' in script
    assert 'credentials: "omit"' in script and 'redirect: "error"' in script
    loader = (root / "telegram_loader.js").read_text()
    assert 'dataset.preview === "true") return' in loader
    assert "https://telegram.org/js/telegram-web-app.js" in loader
