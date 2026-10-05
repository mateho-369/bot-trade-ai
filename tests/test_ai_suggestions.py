from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from ai.suggestion_store import SuggestionStore
from core.database import Database
from core.models import AISuggestion, BotState, RiskState
from tests.risk_helpers import MOMENT, OWNER, config
from trading.risk_types import RuntimeProfile
from trading.types import ManualClock, SourceKind, TradingDisabled


@pytest.fixture
def store(tmp_path):
    cfg = config(tmp_path)
    db = Database(cfg)
    db.initialize()
    clock = ManualClock(MOMENT)
    store = SuggestionStore(db, cfg, clock, RuntimeProfile.current(cfg, SourceKind.SYNTHETIC))
    yield store
    db.close()


def make(store, **kwargs):
    return store.create(
        "reduce_risk", {"risk_percent": "0.2"}, reason="Owner review only.", request_hash="a" * 64, **kwargs
    )


def test_owner_projection_is_new_frozen_settings_and_preserves_latches(store):
    suggestion = make(store)
    assert suggestion.status == "pending"
    with pytest.raises(TradingDisabled):
        store.apply(suggestion.suggestion_id, owner_id=OWNER)
    store.decide(suggestion.suggestion_id, owner_id=OWNER, approve=True)
    with store.database.session() as s:
        state = s.get(BotState, 1)
        state.kill_switch_active = True
        state.desired_state = "killed"
        s.add(
            RiskState(
                account_key="fixture",
                mode="paper",
                day=MOMENT.date(),
                day_start_equity=Decimal("900"),
                equity_high_water=Decimal("1100"),
                net_realized_today=Decimal("-20"),
                accepted_entries_today=7,
                daily_loss_latched=True,
                drawdown_latched=True,
            )
        )
    changed = store.apply(suggestion.suggestion_id, owner_id=OWNER)
    assert changed.effective_risk_percent == Decimal(
        ".2"
    ) and store.settings.effective_risk_percent == Decimal(".5")
    with store.database.session() as s:
        state = s.get(BotState, 1)
        risk = s.scalar(select(RiskState))
        assert state.kill_switch_active and state.desired_state == "killed" and state.session_id is None
        assert (
            risk.daily_loss_latched
            and risk.drawdown_latched
            and risk.accepted_entries_today == 7
            and risk.equity_high_water == 1100
        )
    assert store.get(suggestion.suggestion_id).status == "applied"
    with pytest.raises(TradingDisabled):
        store.apply(suggestion.suggestion_id, owner_id=OWNER)
    assert not (store.settings.project_root / ".env").exists()


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("reduce_risk", {"risk_percent": "0.6"}),
        ("reduce_risk", {"risk_percent": "0.5"}),
        ("reduce_risk", {"risk_percent": 0.2}),
        ("reduce_risk", {"risk_percent": "0"}),
        ("reduce_risk", {"risk_percent": "0.2", "live_trading": True}),
        ("withdraw", {"amount": "5"}),
        ("rebalance_weights", {"weights": {"trend": "1"}}),
        (
            "rebalance_weights",
            {"weights": {"trend": "0.33", "mean_reversion": "0.22", "breakout": "0.20", "momentum": "0.25"}},
        ),
        (
            "close_position",
            {"trade_id": True, "position_identifier": 2, "position_hash": "a" * 64, "fraction": "1"},
        ),
    ],
)
def test_unbounded_or_unsafe_proposals_never_stored(store, kind, payload):
    with pytest.raises(TradingDisabled):
        store.create(kind, payload, reason="unsafe", request_hash="a" * 64)
    with store.database.session() as s:
        assert not s.scalars(select(AISuggestion)).all()


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_owner",
        "bool_owner",
        "tampered",
        "expired",
        "decision_flip",
        "running",
        "lease",
        "reserved",
        "stale_projection",
    ],
)
def test_owner_expiry_integrity_and_stopped_guard(store, fault):
    suggestion = make(store)
    if fault == "tampered":
        with store.database.session() as s:
            row = s.get(AISuggestion, suggestion.suggestion_id)
            row.suggestion = {**row.suggestion, "parameters": {"risk_percent": "0.9"}}
        with pytest.raises(TradingDisabled):
            store.get(suggestion.suggestion_id)
        return
    if fault in {"wrong_owner", "bool_owner"}:
        with pytest.raises(TradingDisabled):
            store.decide(
                suggestion.suggestion_id, owner_id=OWNER + 1 if fault == "wrong_owner" else True, approve=True
            )
        return
    if fault == "expired":
        store.clock.advance(timedelta(seconds=3601))
        assert store.get(suggestion.suggestion_id).status == "expired"
        with pytest.raises(TradingDisabled):
            store.decide(suggestion.suggestion_id, owner_id=OWNER, approve=True)
        return
    store.decide(suggestion.suggestion_id, owner_id=OWNER, approve=True)
    if fault == "decision_flip":
        with pytest.raises(TradingDisabled):
            store.decide(suggestion.suggestion_id, owner_id=OWNER, approve=False)
        return
    with store.database.session() as s:
        state = s.get(BotState, 1)
        if fault == "running":
            state.desired_state = "running"
        if fault == "lease":
            state.session_id = "live-lease"
            state.heartbeat = MOMENT
        if fault == "stale_projection":
            state.settings_overrides = {"new_hash": "f" * 64}
        if fault == "reserved":
            s.add(
                RiskState(
                    account_key="fixture",
                    mode="paper",
                    day=MOMENT.date(),
                    day_start_equity=Decimal("1000"),
                    equity_high_water=Decimal("1000"),
                    reserved_risk_usd=Decimal("5"),
                )
            )
    with pytest.raises(TradingDisabled):
        store.apply(suggestion.suggestion_id, owner_id=OWNER)


def test_bounded_owner_weight_change_and_close_never_executes(store):
    weights = {"trend": "0.32", "mean_reversion": "0.25", "breakout": "0.18", "momentum": "0.25"}
    suggestion = store.create(
        "rebalance_weights",
        {"weights": weights},
        reason="Independent revalidation needed.",
        request_hash="a" * 64,
    )
    store.decide(suggestion.suggestion_id, owner_id=OWNER, approve=True)
    changed = store.apply(suggestion.suggestion_id, owner_id=OWNER)
    assert sum(changed.strategy_weights.values()) == 1 and changed.strategy_weights["trend"] == Decimal(".32")
    assert changed.max_risk_percent_per_trade == store.settings.max_risk_percent_per_trade
    close = store.create(
        "close_position",
        {"trade_id": 1, "position_identifier": 2, "position_hash": "b" * 64, "fraction": "1"},
        reason="Owner-only close proposal.",
        request_hash="c" * 64,
        ttl_seconds=30,
    )
    store.decide(close.suggestion_id, owner_id=OWNER, approve=True)
    with pytest.raises(TradingDisabled):
        store.apply(close.suggestion_id, owner_id=OWNER)


def test_cross_thread_dedup_and_secret_sanitizing(store):
    with ThreadPoolExecutor(max_workers=4) as p:
        rows = list(p.map(lambda _: make(store), range(8)))
    assert len({r.suggestion_id for r in rows}) == 1
    row = store.create(
        "reduce_risk", {"risk_percent": "0.1"}, reason="password=SHOULD_NOT_PERSIST", request_hash="d" * 64
    )
    assert "SHOULD_NOT_PERSIST" not in row.payload()["reason"]
