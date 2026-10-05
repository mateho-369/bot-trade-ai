"""Fresh portfolio loss-to-stop and signed account/USD snapshot valuation."""

from decimal import Decimal

from core.settings import Settings
from trading.authorization import BrokerSnapshot, PositionRisk
from trading.currency import CurrencyConverter
from trading.price_rules import adverse_price
from trading.types import ZERO, AccountInfo, MarketData, Position


async def broker_snapshot(
    market: MarketData,
    settings: Settings,
    account: AccountInfo,
    symbol,
    tick,
    positions: tuple[Position, ...],
    risk=ZERO,
    margin=ZERO,
    reward=ZERO,
    *,
    entry: bool = True,
    durable_simulation: bool = False,
) -> BrokerSnapshot:
    converter = CurrencyConverter(market, settings)
    asset, liability = None, None
    valuations = []
    if entry:
        asset = await converter.convert(Decimal("1"), account.currency, "USD")
        liability = -(await converter.convert(Decimal("-1"), account.currency, "USD"))
        for position in positions:
            meta, quote = (
                await market.get_symbol_info(position.symbol),
                await market.get_tick(position.symbol),
            )
            quote.fresh(market.clock, settings.max_tick_age_seconds)
            if position.sl <= ZERO:
                valuations.append(PositionRisk(position.identifier, account.risk_capital))
                continue
            stop_exit = adverse_price(
                position.sl, position.side, meta, settings.max_slippage_points, entry=False
            )
            at_stop = await market.calculate_profit(
                position.symbol, position.side, position.volume, position.entry_price, stop_exit
            )
            current = await market.calculate_profit(
                position.symbol,
                position.side,
                position.volume,
                position.entry_price,
                quote.exit(position.side),
            )
            cost = -(
                await converter.convert(
                    -settings.commission_round_turn_usd_per_lot * position.volume, "USD", account.currency
                )
            )
            # Count both initial nominal loss and current marked-equity downside.
            loss = max(ZERO, -at_stop - position.swap + cost, current - at_stop + cost / 2)
            valuations.append(PositionRisk(position.identifier, loss))
    return BrokerSnapshot(
        account,
        symbol,
        tick,
        positions,
        risk,
        margin,
        reward,
        market.clock.now(),
        tuple(valuations),
        asset,
        liability,
        market.source_kind,
        durable_simulation,
    )
