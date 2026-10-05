from dataclasses import replace
from decimal import Decimal

import pytest

from tests.risk_helpers import MOMENT, config
from tests.signal_helpers import news
from trading.risk_types import PositionReview, RuntimeProfile, position_review_hash
from trading.types import ManualClock, Position, Side, SourceKind


@pytest.fixture
def sample(tmp_path):
    cfg = config(tmp_path, allow_tp_extension=True)
    clock = ManualClock(MOMENT)
    profile = RuntimeProfile.current(cfg, SourceKind.MT5)
    position = Position(
        100,
        200,
        "EURUSD",
        Side.BUY,
        Decimal(".02"),
        Decimal("1.1"),
        Decimal("1.101"),
        Decimal("1.103"),
        MOMENT,
        cfg.mt5_magic_number,
    )
    review = PositionReview(
        MOMENT,
        SourceKind.MT5,
        90,
        True,
        True,
        news(clock),
        position.identifier,
        position_review_hash(position),
        profile.code_hash,
        profile.model_sha256,
    )
    return cfg, clock, profile, position, review


def test_native_binding_and_improved_sl_not_invalidated(sample):
    cfg, clock, profile, position, review = sample
    assert review.allows_extension(cfg, clock.now(), SourceKind.MT5, position=position, profile=profile)
    assert review.allows_extension(
        cfg,
        clock.now(),
        SourceKind.MT5,
        position=replace(position, sl=position.sl + Decimal(".0001")),
        profile=profile,
    )
    legacy = PositionReview(MOMENT, SourceKind.MT5, 90, True, True, news(clock))
    assert not legacy.allows_extension(cfg, clock.now(), SourceKind.MT5, position=position, profile=profile)


@pytest.mark.parametrize("fault", ["ticket", "identifier", "volume", "tp", "entry", "side", "code", "model"])
def test_bound_review_cannot_be_reused_for_another_position_or_profile(sample, fault):
    cfg, clock, profile, position, review = sample
    if fault == "ticket":
        position = replace(position, ticket=101)
    if fault == "identifier":
        position = replace(position, identifier=201)
    if fault == "volume":
        position = replace(position, volume=Decimal(".01"))
    if fault == "tp":
        position = replace(position, tp=position.tp + Decimal(".0001"))
    if fault == "entry":
        position = replace(position, entry_price=position.entry_price + Decimal(".0001"))
    if fault == "side":
        position = replace(position, side=Side.SELL)
    if fault == "code":
        profile = replace(profile, code_hash="f" * 64)
    if fault == "model":
        profile = replace(profile, model_sha256="f" * 64)
    assert not review.allows_extension(cfg, clock.now(), SourceKind.MT5, position=position, profile=profile)
