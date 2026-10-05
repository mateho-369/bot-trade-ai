"""Shared async conveniences; account currency and USD methods are distinct."""

from decimal import Decimal

from trading.types import Side


class ClientCalculations:
    async def get_balance(self) -> Decimal:
        return (await self.get_account_info()).balance

    async def get_equity(self) -> Decimal:
        return (await self.get_account_info()).equity

    async def get_open_positions(self):
        return await self.get_positions()

    def _calculator(self):
        from trading.order_calculator import OrderCalculator

        return OrderCalculator(self, self.settings, self.clock)

    async def calculate_lot_size(self, symbol: str, side: Side, entry: Decimal, sl: Decimal, **kwargs):
        return await self._calculator().calculate_lot_size(symbol, side, entry, sl, **kwargs)

    async def calculate_profit_usd(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal, **kwargs
    ) -> Decimal:
        return await self._calculator().calculate_profit_usd(
            symbol, side, volume, entry, exit_price, **kwargs
        )

    async def calculate_price_distance_from_usd_profit(
        self, symbol: str, side: Side, volume: Decimal, entry: Decimal, target_usd: Decimal, **kwargs
    ) -> Decimal:
        return await self._calculator().calculate_price_distance_from_usd_profit(
            symbol, side, volume, entry, target_usd, **kwargs
        )
