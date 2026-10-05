"""Market Awareness Engine: the bounded, READ-ONLY context the AI-first brain sees.

Gathers, for one symbol: quote/spread, multi-timeframe indicators (RSI, MACD, EMA trend, ATR,
Bollinger, ADX), the last 5 OHLC candles plus a 20-bar price-action summary, support/resistance,
volatility regime, open positions and floating P&L, news state/sentiment/upcoming events, current
config limits (incl. the AI overlay) and recent trade performance.

It never sends, modifies or closes anything; it only calls MarketData read methods. Missing data
is reported as ``None``/"unknown", never fabricated. The snapshot digest keys the brain's cache.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
from sqlalchemy import select

from core.database import Database
from core.models import Trade
from core.security import sha256_json
from core.settings import Settings
from strategy.indicators import calculate_indicators
from trading.risk_types import NewsWindow
from trading.types import BrokerError, Clock, MarketData, Side

PROMPT_CANDLES = 5
PRICE_ACTION_BARS = 20
UPCOMING_EVENT_HOURS = 2


def _num(value, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return round(number, digits) if math.isfinite(number) else None


@dataclass(frozen=True, slots=True)
class NewsContext:
    """News view for the AI. ``state`` is clear/blocked/unknown; unknown is NEVER clearance."""

    state: str = "unknown"
    sentiment: float | None = None
    upcoming_events: tuple[dict, ...] = ()
    headlines: tuple[str, ...] = ()

    @classmethod
    def from_window(
        cls,
        window: NewsWindow | None,
        *,
        sentiment: float | None = None,
        events: tuple[dict, ...] = (),
        headlines: tuple[str, ...] = (),
    ) -> NewsContext:
        if window is None or not window.known:
            state = "unknown"
        else:
            state = "clear" if window.safe else "blocked"
        if sentiment is not None and not -1 <= float(sentiment) <= 1:
            raise ValueError("sentiment must be within -1..1")
        return cls(
            state,
            None if sentiment is None else float(sentiment),
            tuple(events)[:10],
            tuple(str(h)[:160] for h in headlines)[:5],
        )

    def risk_level(self, now: datetime) -> str:
        if self.state != "clear":
            return "high"
        soon = [e for e in self.upcoming_events if e.get("impact") == "high"]
        if any(0 <= float(e.get("minutes_until", 9999)) <= 30 for e in soon):
            return "high"
        if soon or any(e.get("impact") == "medium" for e in self.upcoming_events):
            return "medium"
        return "low"


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    native: str
    observed_at: datetime
    quote: dict
    timeframes: dict
    candles: tuple[dict, ...]
    price_action: dict
    levels: dict
    regime: str
    technical: dict
    positions: tuple[dict, ...]
    news: dict
    config: dict
    performance: dict = field(default_factory=dict)
    lessons: tuple[str, ...] = ()

    def to_prompt(self) -> dict:
        return {
            "format": "reflex-market-snapshot-v1",
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "quote": self.quote,
            "timeframes": self.timeframes,
            "last_candles": list(self.candles),
            "price_action_20": self.price_action,
            "support_resistance": self.levels,
            "market_condition_hint": self.regime,
            "technical_signal": self.technical,
            "open_positions": list(self.positions),
            "news": self.news,
            "config": self.config,
            "performance": self.performance,
            "lessons_learned": list(self.lessons),
        }

    @property
    def digest(self) -> str:
        """Cache key. Minute-bucketed so identical market states within a bar share one AI call."""
        payload = self.to_prompt()
        payload["observed_at"] = self.observed_at.replace(second=0, microsecond=0).isoformat()
        return sha256_json(payload)

    def summary(self) -> dict:
        """Compact journal summary (no candles/headlines)."""
        primary = next(iter(self.timeframes.values()), {}) if self.timeframes else {}
        return {
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "bid": self.quote.get("bid"),
            "ask": self.quote.get("ask"),
            "spread_points": self.quote.get("spread_points"),
            "atr": primary.get("atr"),
            "rsi": primary.get("rsi"),
            "trend": {tf: v.get("ema_trend") for tf, v in self.timeframes.items()},
            "regime": self.regime,
            "technical": self.technical,
            "news_state": self.news.get("state"),
            "news_risk": self.news.get("risk"),
            "open_positions": len(self.positions),
            "digest": self.digest,
        }


class PerformanceTracker:
    """Requirement 3c: rolling performance read from the durable trade ledger (read-only)."""

    def __init__(self, database: Database, clock: Clock):
        self.database, self.clock = database, clock

    def summary(self, account_key: str | None = None) -> dict:
        now = self.clock.now()
        with self.database.session() as session:
            query = select(Trade).where(
                Trade.close_time.is_not(None), Trade.close_time >= now - timedelta(days=7)
            )
            if account_key is not None:
                query = query.where(Trade.account_key == account_key)
            rows = session.scalars(query.order_by(Trade.close_time.desc()).limit(500)).all()
            trades = [
                (
                    row.close_time,
                    Decimal(row.profit_usd),
                    row.strategy,
                    row.open_time,
                    Decimal(row.commission),
                )
                for row in rows
            ]

        def window(hours):
            items = [t for t in trades if t[0] >= now - timedelta(hours=hours)]
            if not items:
                return {"trades": 0, "win_rate": None, "avg_pnl_usd": None}
            wins = sum(t[1] > 0 for t in items)
            return {
                "trades": len(items),
                "win_rate": round(wins / len(items) * 100, 2),
                "avg_pnl_usd": str(sum((t[1] for t in items), Decimal("0")) / len(items)),
            }

        by_strategy, by_hour = defaultdict(lambda: Decimal("0")), defaultdict(lambda: Decimal("0"))
        for _, pnl, strategy, opened, _ in trades:
            by_strategy[strategy] += pnl
            by_hour[opened.hour] += pnl
        ranked = sorted(by_strategy.items(), key=lambda kv: kv[1])
        hours = sorted(by_hour.items(), key=lambda kv: kv[1])
        costs = sum((abs(t[4]) for t in trades), Decimal("0"))
        gross = sum((abs(t[1]) for t in trades), Decimal("0"))
        return {
            "last_24h": window(24),
            "last_7d": window(24 * 7),
            "best_strategy": ranked[-1][0] if ranked else None,
            "worst_strategy": ranked[0][0] if ranked else None,
            "best_hour_utc": hours[-1][0] if hours else None,
            "worst_hour_utc": hours[0][0] if hours else None,
            "cost_share_percent": None if not gross else _num(costs / gross * 100, 2),
        }


class MarketAwarenessEngine:
    def __init__(
        self,
        broker: MarketData,
        settings: Settings,
        clock: Clock,
        *,
        performance: PerformanceTracker | None = None,
        overlay=None,
        journal=None,
    ):
        self.broker, self.settings, self.clock = broker, settings, clock
        self.performance, self.overlay, self.journal = performance, overlay, journal

    def native_symbol(self, logical: str) -> str:
        return self.settings.symbol_aliases.get(logical, logical)

    async def _timeframe(self, native: str, timeframe: str) -> tuple[dict, pd.DataFrame | None]:
        try:
            count = max(self.settings.candle_lookback, 220)
            candles = await self.broker.get_candles(native, timeframe, count)
            frame = calculate_indicators(candles)
        except (BrokerError, KeyError, ValueError, TypeError):
            return {"available": False}, None
        last = frame.iloc[-1]
        fast, mid, slow = (_num(last.get(k)) for k in ("ema_fast", "ema_mid", "ema_slow"))
        if None in (fast, mid, slow):
            trend = "unknown"
        elif fast > mid > slow:
            trend = "up"
        elif fast < mid < slow:
            trend = "down"
        else:
            trend = "flat"
        atr, median = _num(last.get("atr")), _num(last.get("atr_median"))
        return (
            {
                "available": True,
                "close": _num(last.get("close")),
                "rsi": _num(last.get("rsi"), 2),
                "macd_hist_atr": _num(last.get("macd_hist_atr"), 4),
                "ema_trend": trend,
                "atr": atr,
                "atr_percent": _num(last.get("atr_percent"), 4),
                "atr_vs_median": None if not atr or not median else _num(atr / median, 3),
                "adx": _num(last.get("adx"), 2),
                "bb_position": _num(last.get("bb_position"), 3),
                "bb_width_percent": _num(last.get("bb_width_percent"), 4),
                "volume_ratio": _num(last.get("volume_ratio"), 3),
                "efficiency": _num(last.get("efficiency"), 3),
            },
            frame,
        )

    @staticmethod
    def regime(primary: dict, news_state: str) -> str:
        if news_state == "blocked":
            return "news_impact"
        ratio = primary.get("atr_vs_median")
        if ratio is not None and ratio >= 1.8:
            return "volatile"
        adx, efficiency = primary.get("adx"), primary.get("efficiency")
        if adx is not None and adx >= 25 and (efficiency or 0) >= 0.3:
            return "trending"
        return "ranging"

    @staticmethod
    def local_technical(timeframes: dict) -> dict:
        """Deterministic multi-timeframe score used ONLY when no strategy proposal is supplied."""
        votes, weight = 0.0, 0.0
        for index, values in enumerate(timeframes.values()):
            if not values.get("available"):
                continue
            w = 1.0 + 0.5 * index  # Higher timeframes weigh more.
            weight += 2 * w
            votes += {"up": w, "down": -w}.get(values.get("ema_trend"), 0.0)
            macd = values.get("macd_hist_atr")
            if macd is not None:
                votes += w * (0.5 if macd > 0 else -0.5 if macd < 0 else 0)
            rsi = values.get("rsi")
            if rsi is not None:
                votes += w * (0.5 if rsi >= 55 else -0.5 if rsi <= 45 else 0)
        if weight == 0:
            return {"side": None, "score": 0.0, "source": "local_multi_timeframe"}
        strength = abs(votes) / weight
        side = None if strength < 0.25 else ("buy" if votes > 0 else "sell")
        return {"side": side, "score": round(50 + 50 * strength, 2), "source": "local_multi_timeframe"}

    async def snapshot(
        self,
        logical: str,
        *,
        native: str | None = None,
        technical_side: Side | None = None,
        technical_score: float | None = None,
        stop_price: Decimal | None = None,
        news: NewsContext | None = None,
        account_key: str | None = None,
    ) -> MarketSnapshot:
        native = native or self.native_symbol(logical)
        now = self.clock.now()
        meta = await self.broker.get_symbol_info(native)
        tick = await self.broker.get_tick(native)
        tick.fresh(self.clock, self.settings.max_tick_age_seconds)
        spread = tick.spread_points(meta)
        overlay = self.overlay.effective() if self.overlay is not None else {}
        spread_limit = min(
            self.settings.spread_limit_points(logical, native),
            overlay.get("max_spread_points", 10**9),
        )
        quote = {
            "bid": _num(tick.bid, 10),
            "ask": _num(tick.ask, 10),
            "spread_points": _num(spread, 2),
            "spread_limit_points": spread_limit,
            "point": _num(meta.point, 10),
            "tick_age_seconds": _num((now - tick.time).total_seconds(), 1),
        }
        frames = (
            self.settings.primary_timeframe,
            self.settings.higher_timeframe,
            self.settings.trend_timeframe,
        )
        timeframes, primary_frame = {}, None
        for timeframe in dict.fromkeys(frames):
            values, frame = await self._timeframe(native, timeframe)
            timeframes[timeframe] = values
            if timeframe == self.settings.primary_timeframe:
                primary_frame = frame
        candles, price_action, levels = (), {}, {}
        if primary_frame is not None:
            tail = primary_frame.tail(PRICE_ACTION_BARS)
            candles = tuple(
                {
                    "time": pd.Timestamp(row["time"]).isoformat(),
                    "open": _num(row["open"], 10),
                    "high": _num(row["high"], 10),
                    "low": _num(row["low"], 10),
                    "close": _num(row["close"], 10),
                }
                for _, row in tail.tail(PROMPT_CANDLES).iterrows()
            )
            first, last = float(tail["close"].iloc[0]), float(tail["close"].iloc[-1])
            price_action = {
                "bars": len(tail),
                "high": _num(tail["high"].max(), 10),
                "low": _num(tail["low"].min(), 10),
                "net_change": _num(last - first, 10),
                "up_bars": int((tail["close"] > tail["open"]).sum()),
                "down_bars": int((tail["close"] < tail["open"]).sum()),
            }
            final = primary_frame.iloc[-1]
            levels = {
                "resistance_20": _num(final.get("channel_high"), 10),
                "support_20": _num(final.get("channel_low"), 10),
                "swing_high_5": _num(final.get("swing_high"), 10),
                "swing_low_5": _num(final.get("swing_low"), 10),
            }
        news = news or NewsContext()
        news_view = {
            "state": news.state,
            "risk": news.risk_level(now),
            "sentiment": news.sentiment,
            "upcoming_events_2h": list(news.upcoming_events),
            "headlines": list(news.headlines),
        }
        primary = timeframes.get(self.settings.primary_timeframe, {})
        regime = self.regime(primary, news.state)
        if technical_side is not None or technical_score is not None:
            technical = {
                "side": None if technical_side is None else technical_side.value.lower(),
                "score": None if technical_score is None else round(float(technical_score), 2),
                "stop_price": None if stop_price is None else _num(stop_price, 10),
                "source": "strategy_router",
            }
        else:
            technical = self.local_technical(timeframes)
        try:
            positions = tuple(
                {
                    "symbol": p.symbol,
                    "side": p.side.value.lower(),
                    "volume": str(p.volume),
                    "entry": str(p.entry_price),
                    "sl": str(p.sl),
                    "tp": str(p.tp),
                    "floating_profit_account": str(p.profit),
                }
                for p in (await self.broker.get_positions())[:10]
            )
        except BrokerError:
            positions = ()
        cfg = self.settings
        config = {
            "risk_percent_per_trade": str(overlay.get("risk_percent_per_trade", cfg.effective_risk_percent)),
            "owner_risk_ceiling_percent": str(cfg.effective_risk_percent),
            "target_profit_usd": str(overlay.get("target_profit_per_trade", cfg.target_profit_usd_per_trade)),
            "max_daily_trades": overlay.get("max_daily_trades", cfg.max_daily_trades),
            "max_open_positions": overlay.get("max_open_positions", cfg.max_open_positions),
            "dynamic_limits_source": overlay.get("limits_source", "defaults"),
            "hard_caps": {"max_daily_trades": 25, "max_open_positions": 5, "risk_percent": "1.0"},
            "max_daily_loss_percent": str(cfg.max_daily_loss_percent),
            "max_drawdown_percent": str(cfg.max_drawdown_percent),
            "trailing_levels": [list(level) for level in cfg.trailing_levels],
            "strategy_weights": {k: str(v) for k, v in cfg.strategy_weights.items()},
            "ai_confidence_threshold": cfg.ai_confidence_threshold,
            "mode": cfg.mode.value,
        }
        performance = {}
        if self.performance is not None:
            performance = self.performance.summary(account_key)
        lessons = tuple(self.journal.lessons(limit=5)) if self.journal is not None else ()
        return MarketSnapshot(
            logical,
            native,
            now,
            quote,
            timeframes,
            candles,
            price_action,
            levels,
            regime,
            technical,
            positions,
            news_view,
            config,
            performance,
            lessons,
        )
