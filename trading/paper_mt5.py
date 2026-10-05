"""Real market data + ONLY simulated orders. The source is never used to trade."""

from typing import Any

from core.settings import OperatingMode, Settings
from trading.simulation import SimulatedBroker
from trading.types import MarketData, SourceKind, TradingDisabled


class PaperMT5Client(SimulatedBroker):
    def __init__(
        self,
        market: MarketData,
        settings: Settings,
        *,
        ledger_id: str = "paper-main",
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        if settings.mode != OperatingMode.PAPER:
            raise TradingDisabled("PaperMT5Client requires PAPER_TRADING=true")
        super().__init__(
            market, settings, source_kind=SourceKind.PAPER, ledger_id=ledger_id, restored_state=restored_state
        )

    @property
    def market_source_kind(self) -> SourceKind:
        return self.market.source_kind
