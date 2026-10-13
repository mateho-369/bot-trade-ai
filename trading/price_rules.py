"""Pure broker-grid rules; never round a lot UP to meet the broker minimum."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from core.settings import Settings
from trading.types import (
    ZERO,
    BrokerCommand,
    Clock,
    InvalidOrder,
    MarketOrder,
    Position,
    RiskViolation,
    Side,
    SymbolInfo,
    Tick,
    aware_utc,
)


def snap(price: Decimal, step: Decimal, *, up: bool) -> Decimal:
    if step <= ZERO or not price.is_finite():
        raise InvalidOrder("invalid price grid")
    rounding = ROUND_CEILING if up else ROUND_FLOOR
    return (price / step).to_integral_value(rounding=rounding) * step


def adverse_price(price: Decimal, side: Side, symbol: SymbolInfo, points: int, *, entry: bool) -> Decimal:
    """Round estimated slippage against us to an ACTUAL executable tick.

    A ten-point allowance need not be a whole native tick (e.g. 0.10 vs 0.25).
    This is a nominal estimate, not a guarantee against market gaps.
    """
    if not isinstance(points, int) or isinstance(points, bool) or points < 0:
        raise InvalidOrder("invalid point-based slippage allowance")
    direction = side.sign if entry else -side.sign
    return snap(price + direction * points * symbol.point, symbol.tick_size, up=direction > ZERO)


def floor_volume(volume: Decimal, symbol: SymbolInfo) -> Decimal:
    volume = min(volume, symbol.volume_max)
    result = snap(volume, symbol.volume_step, up=False)
    return ZERO if result < symbol.volume_min else result


def validate_volume(volume: Decimal, symbol: SymbolInfo) -> None:
    if not volume.is_finite() or not symbol.volume_min <= volume <= symbol.volume_max:
        raise InvalidOrder("volume violates min/max")
    if volume % symbol.volume_step != ZERO:
        raise InvalidOrder("volume violates broker step")
    if symbol.volume_limit > ZERO and volume > symbol.volume_limit:
        raise InvalidOrder("volume exceeds the broker directional limit")


def validate_price(price: Decimal, symbol: SymbolInfo) -> None:
    if not price.is_finite() or price <= ZERO or price % symbol.tick_size != ZERO:
        raise InvalidOrder("price is not on the positive broker tick grid")


def validate_levels(
    symbol: SymbolInfo, tick: Tick, side: Side, sl: Decimal, tp: Decimal, *, modifying: bool = False
) -> None:
    distance = max(symbol.stops_level, symbol.freeze_level if modifying else 0) * symbol.point
    market = tick.exit(side)
    for price in (sl, tp):
        if price > ZERO:
            validate_price(price, symbol)
    if sl > ZERO and (side.sign * (market - sl) <= ZERO or side.sign * (market - sl) < distance):
        raise InvalidOrder("SL violates stop/freeze distance")
    if tp > ZERO and (side.sign * (tp - market) <= ZERO or side.sign * (tp - market) < distance):
        raise InvalidOrder("TP violates stop/freeze distance")
    if modifying and symbol.freeze_level:
        for price in (sl, tp):
            if price > ZERO and abs(price - market) <= symbol.freeze_level * symbol.point:
                raise InvalidOrder("SL/TP is inside the freeze zone")


def validate_entry(
    order: MarketOrder, symbol: SymbolInfo, tick: Tick, settings: Settings, clock: Clock
) -> None:
    enabled = {settings.symbol_aliases.get(name, name) for name in settings.symbols}
    if order.symbol not in enabled:
        raise InvalidOrder("symbol is not enabled by the owner")
    age = (aware_utc(clock.now()) - aware_utc(order.created_at)).total_seconds()
    if age < -2 or age > settings.order_max_age_seconds:
        raise InvalidOrder("order is stale or from the future")
    tick.fresh(clock, settings.max_tick_age_seconds)
    validate_volume(order.volume, symbol)
    validate_price(order.reference_price, symbol)
    validate_levels(symbol, tick, order.side, order.sl, order.tp)
    if not symbol.order_mode & 1 or not symbol.order_mode & 16 or not symbol.order_mode & 32:
        raise InvalidOrder("broker does not advertise native SL and TP support")
    allowed = (
        symbol.trade_mode == 4
        or (symbol.trade_mode == 1 and order.side == Side.BUY)
        or (symbol.trade_mode == 2 and order.side == Side.SELL)
    )
    if not allowed:
        raise InvalidOrder("symbol trading mode forbids this entry")
    # Overrides may be keyed by logical name (preferred) or native broker name.
    logical = next(
        (name for name in settings.symbols if settings.symbol_aliases.get(name, name) == order.symbol), None
    )
    limit = settings.spread_limit_points(logical, order.symbol)
    if tick.spread_points(symbol) > limit:
        raise RiskViolation("spread exceeds the configured limit")
    if abs(tick.entry(order.side) - order.reference_price) > settings.max_slippage_points * symbol.point:
        raise RiskViolation("price moved outside the approved reference/deviation window")


def protection_prices(
    command: BrokerCommand, position: Position, symbol: SymbolInfo, tick: Tick, settings: Settings
) -> tuple[Decimal, Decimal]:
    sl = position.sl if command.sl is None else command.sl
    tp = position.tp if command.tp is None else command.tp
    if sl <= ZERO or tp < ZERO or (command.tp is not None and tp <= ZERO):
        raise InvalidOrder("cannot remove protection")
    if position.sl > ZERO and position.side.sign * (sl - position.sl) < ZERO:
        raise RiskViolation("SL modification would loosen protection")
    if symbol.freeze_level:
        for existing in (position.sl, position.tp):
            if (
                existing > ZERO
                and abs(existing - tick.exit(position.side)) <= symbol.freeze_level * symbol.point
            ):
                raise InvalidOrder("existing protection is frozen")
    validate_levels(symbol, tick, position.side, sl, tp, modifying=True)
    extending = tp > ZERO and position.tp > ZERO and position.side.sign * (tp - position.tp) > ZERO
    if extending and not settings.allow_tp_extension:
        raise RiskViolation("TP extension is disabled")
    return sl, tp
