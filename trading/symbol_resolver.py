"""Dynamic broker symbol discovery: suffixes, contract specs, spread caps, exposure.

Brokers rename instruments per account type: ``XAUUSD.``, ``XAUUSDm`` (Exness
Standard), ``XAUUSDc`` (Exness Cent), ``EURUSD.raw``, ``US30-ecn``. The owner
configures LOGICAL names (``SYMBOLS=XAUUSD,USDJPY,US30``); this module finds the
broker's actual name for each one from the terminal's symbol list and reads the
contract metadata the risk engine already uses (contract size, tick size, tick
value, lot limits).

Discovery only READS broker metadata. Its result is persisted to ``.env`` by
``python -m scripts.resolve_symbols --write`` so that the runtime, the watchdog,
backups and the database all share ONE configuration fingerprint. Nothing is
silently re-bound at runtime: an unresolved or ambiguous symbol stays disabled.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from core.settings import Settings
from trading.currency import account_currency_profile, cent_parent
from trading.types import BrokerError, MarketData, SymbolInfo

# Separator-led suffixes (".", ".raw", "-ecn", "_i", "#", "+") or a short
# LOWERCASE tail ("m", "c", "z", "pro"). An uppercase tail is a different
# instrument (ETHUSD vs ETHUSDT), never a suffix.
_SUFFIX = re.compile(r"(?:[._\-#+!][A-Za-z0-9]{0,8}|[a-z][a-z0-9]{0,3})")

# Currencies whose economic calendars are meaningful news exposure. Metals,
# crypto and index codes (XAU, BTC, ETH, ...) are not, so XAUUSD -> ("USD",).
FIAT_NEWS_CURRENCIES = frozenset(
    "USD EUR GBP JPY CHF AUD NZD CAD CNY CNH HKD SGD SEK NOK DKK PLN HUF CZK TRY ZAR "
    "MXN BRL INR KRW THB IDR MYR PHP ILS RUB AED SAR".split()
)

DEFAULT_SPREAD_MULTIPLIER = Decimal("2")
MAX_SPREAD_LIMIT = 100000  # Settings bound for SYMBOL_SPREAD_LIMITS_JSON.


def broker_suffix(logical: str, native: str) -> str | None:
    """Suffix that turns ``logical`` into ``native`` ("" if identical), else None."""
    if native == logical:
        return ""
    if not native.startswith(logical):
        return None
    rest = native[len(logical) :]
    return rest if _SUFFIX.fullmatch(rest) else None


@dataclass(frozen=True, slots=True)
class SymbolResolution:
    natives: dict[str, str]  # logical -> broker name, every resolved symbol
    methods: dict[str, str]  # logical -> explicit_alias | exact | suffix | preferred_suffix | suffix_vote
    errors: dict[str, str]  # logical -> reason it stays disabled
    detected_suffix: str | None = None

    @property
    def aliases(self) -> dict[str, str]:
        """Entries for SYMBOL_ALIASES_JSON (only names that differ)."""
        return {logical: native for logical, native in self.natives.items() if native != logical}


def resolve_symbols(
    logicals: tuple[str, ...] | list[str],
    available: tuple[str, ...] | list[str] | set[str],
    *,
    explicit_aliases: dict[str, str] | None = None,
    preferred_suffix: str | None = None,
    market_watch: tuple[str, ...] | list[str] | set[str] | None = None,
) -> SymbolResolution:
    """Map logical names to broker names. Ambiguity is an error, never a guess."""
    offered = set(available)
    visible = set(market_watch or ())
    explicit = dict(explicit_aliases or {})
    natives: dict[str, str] = {}
    methods: dict[str, str] = {}
    errors: dict[str, str] = {}
    pending: dict[str, dict[str, str]] = {}  # logical -> {suffix: native}
    for logical in logicals:
        if logical in explicit:
            if explicit[logical] in offered:
                natives[logical], methods[logical] = explicit[logical], "explicit_alias"
            else:
                errors[logical] = "configured alias is not offered by this broker"
            continue
        candidates = {
            suffix: native for native in offered if (suffix := broker_suffix(logical, native)) is not None
        }
        if not candidates:
            errors[logical] = "not offered by this broker (no exact or suffixed name)"
        elif preferred_suffix is not None and preferred_suffix in candidates:
            natives[logical], methods[logical] = candidates[preferred_suffix], "preferred_suffix"
        elif len(candidates) == 1:
            suffix, native = next(iter(candidates.items()))
            natives[logical], methods[logical] = native, "exact" if suffix == "" else "suffix"
        else:
            pending[logical] = candidates
    # The account's own naming convention (e.g. every symbol ends in "m") breaks ties.
    votes = Counter(
        broker_suffix(logical, natives[logical])
        for logical in natives
        if methods[logical] != "explicit_alias"
    )
    for logical, candidates in pending.items():
        shown = {suffix: native for suffix, native in candidates.items() if native in visible}
        pool = shown if len(shown) == 1 else candidates
        if len(pool) == 1:
            natives[logical], methods[logical] = next(iter(pool.values())), "market_watch"
            continue
        ranked = sorted(((votes.get(suffix, 0), suffix) for suffix in pool), reverse=True)
        if ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            natives[logical], methods[logical] = pool[ranked[0][1]], "suffix_vote"
        else:
            names = ", ".join(sorted(pool.values()))
            errors[logical] = f"ambiguous broker names ({names}); set SYMBOL_ALIASES_JSON or --suffix"
    duplicates = {native for native, count in Counter(natives.values()).items() if count > 1}
    for logical in [name for name, native in natives.items() if native in duplicates]:
        natives.pop(logical)
        methods.pop(logical)
        errors[logical] = "two logical symbols resolve to the same broker symbol"
    used = Counter(broker_suffix(logical, natives[logical]) for logical in natives)
    detected = used.most_common(1)[0][0] if used else None
    return SymbolResolution(natives, methods, errors, detected)


def infer_news_currencies(info: SymbolInfo) -> tuple[str, ...]:
    """Fiat calendar exposure from the broker's own base/profit currencies."""
    found: list[str] = []
    for code in (info.currency_base.upper(), info.currency_profit.upper()):
        if code in FIAT_NEWS_CURRENCIES and code not in found:
            found.append(code)
    return tuple(found)


def suggest_spread_limit(
    candle_spreads: list[int] | tuple[int, ...],
    current_spread_points: Decimal | None,
    *,
    floor: int,
    multiplier: Decimal = DEFAULT_SPREAD_MULTIPLIER,
) -> int:
    """Instrument-specific cap: typical (median) broker spread x multiplier.

    Never below the owner's global MAX_SPREAD_POINTS. BTCUSD's normal spread can
    be thousands of points while EURUSD's is a handful, so one global number
    either blocks crypto/indices forever or lets FX trade through news spikes.
    The independent spread/ATR filter still applies on top of this cap.
    """
    if not isinstance(multiplier, Decimal) or not multiplier.is_finite() or multiplier < 1:
        raise ValueError("spread multiplier must be a Decimal >= 1")
    usable = [int(value) for value in candle_spreads if int(value) > 0]
    if usable:
        typical = Decimal(str(statistics.median(usable)))
    elif current_spread_points is not None and current_spread_points > 0:
        typical = current_spread_points
    else:
        return floor
    return min(MAX_SPREAD_LIMIT, max(floor, math.ceil(typical * multiplier)))


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    logical: str
    native: str
    method: str
    suffix: str
    info: SymbolInfo
    current_spread_points: Decimal | None
    typical_spread_points: Decimal | None
    configured_spread_limit: int | None
    suggested_spread_limit: int
    configured_news: tuple[str, ...]
    inferred_news: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        info = self.info
        return {
            "logical": self.logical,
            "broker_name": self.native,
            "resolution": self.method,
            "suffix": self.suffix,
            "contract_size": str(info.contract_size),
            "tick_size": str(info.tick_size),
            "tick_value_profit": str(info.tick_value_profit),
            "tick_value_loss": str(info.tick_value_loss),
            "point": str(info.point),
            "digits": info.digits,
            "volume_min": str(info.volume_min),
            "volume_max": str(info.volume_max),
            "volume_step": str(info.volume_step),
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "trade_mode": info.trade_mode,
            "stops_level_points": info.stops_level,
            "current_spread_points": None
            if self.current_spread_points is None
            else str(self.current_spread_points),
            "typical_spread_points": None
            if self.typical_spread_points is None
            else str(self.typical_spread_points),
            "spread_limit_configured": self.configured_spread_limit,
            "spread_limit_suggested": self.suggested_spread_limit,
            "news_currencies_configured": list(self.configured_news),
            "news_currencies_inferred": list(self.inferred_news),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    account_currency: str | None
    declared_currency: str
    resolution: SymbolResolution
    specs: tuple[SymbolSpec, ...]
    conversion_routes: dict[str, str]
    spec_errors: dict[str, str] = field(default_factory=dict)

    def env_updates(self, settings: Settings, *, refresh_spreads: bool = False) -> dict[str, str]:
        """Exact ``.env`` values; owner-configured entries always win."""
        aliases = dict(settings.symbol_aliases)
        aliases.update(self.resolution.aliases)
        spreads = dict(settings.symbol_spread_limits)
        news = {key: list(value) for key, value in settings.symbol_news_currencies.items()}
        for spec in self.specs:
            if refresh_spreads or spec.logical not in spreads:
                spreads[spec.logical] = spec.suggested_spread_limit
            if spec.logical not in news and spec.inferred_news:
                news[spec.logical] = list(spec.inferred_news)
        routes = dict(settings.account_to_usd_symbols)
        for currency, native in self.conversion_routes.items():
            routes.setdefault(currency, native)
        updates = {
            "SYMBOL_ALIASES_JSON": _compact(aliases),
            "SYMBOL_SPREAD_LIMITS_JSON": _compact(spreads),
            "SYMBOL_NEWS_CURRENCIES_JSON": _compact(news),
            "ACCOUNT_TO_USD_SYMBOLS_JSON": _compact(routes),
        }
        if self.account_currency and self.account_currency != self.declared_currency:
            updates["ACCOUNT_CURRENCY"] = self.account_currency
        return updates

    def to_dict(self) -> dict[str, object]:
        currency = self.account_currency or self.declared_currency
        return {
            "diagnostic": "read-only symbol discovery",
            "real_orders_sent": 0,
            "account": account_currency_profile(currency),
            "declared_account_currency": self.declared_currency,
            "account_currency_matches": self.account_currency in (None, self.declared_currency),
            "detected_suffix": self.resolution.detected_suffix,
            "symbols": [spec.to_dict() for spec in self.specs],
            "disabled_symbols": {**self.resolution.errors, **self.spec_errors},
            "conversion_routes": self.conversion_routes,
        }


def _compact(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


async def _typical_spread(market: MarketData, native: str, settings: Settings) -> list[int]:
    try:
        candles = await market.get_candles(native, settings.primary_timeframe, 200)
    except (BrokerError, KeyError, ValueError, TypeError):
        return []
    if "spread" not in candles.columns:
        return []
    return [int(value) for value in candles["spread"].tolist()]


async def discover(
    market: MarketData,
    settings: Settings,
    *,
    preferred_suffix: str | None = None,
    spread_multiplier: Decimal = DEFAULT_SPREAD_MULTIPLIER,
    account_currency: str | None = None,
) -> DiscoveryReport:
    """Read the broker's symbol list and metadata; never trades or selects writes."""
    available = await market.get_symbols()
    watch_reader = getattr(market, "get_market_watch_symbols", None)
    market_watch = await watch_reader() if watch_reader is not None else None
    resolution = resolve_symbols(
        settings.symbols,
        available,
        explicit_aliases=dict(settings.symbol_aliases),
        preferred_suffix=preferred_suffix,
        market_watch=market_watch,
    )
    specs: list[SymbolSpec] = []
    spec_errors: dict[str, str] = {}
    for logical, native in resolution.natives.items():
        try:
            info = await market.select_symbol(native)
            if info.name != native:
                raise BrokerError("broker returned a different symbol")
            current = None
            try:
                current = (await market.get_tick(native)).spread_points(info)
            except BrokerError:
                current = None
            spreads = await _typical_spread(market, native, settings)
            usable = [value for value in spreads if value > 0]
            typical = Decimal(str(statistics.median(usable))) if usable else None
            specs.append(
                SymbolSpec(
                    logical,
                    native,
                    resolution.methods[logical],
                    broker_suffix(logical, native) or "",
                    info,
                    current,
                    typical,
                    settings.symbol_spread_limits.get(logical, settings.symbol_spread_limits.get(native)),
                    suggest_spread_limit(
                        spreads, current, floor=settings.max_spread_points, multiplier=spread_multiplier
                    ),
                    tuple(settings.symbol_news_currencies.get(logical, ())),
                    infer_news_currencies(info),
                )
            )
        except BrokerError:
            spec_errors[logical] = "broker metadata unavailable or invalid"
    currency = account_currency or settings.account_currency
    routes: dict[str, str] = {}
    parent = cent_parent(currency) or currency
    if parent != "USD" and parent not in settings.account_to_usd_symbols:
        suffix = resolution.detected_suffix or ""
        for pair in (parent + "USD", "USD" + parent):
            found = resolve_symbols((pair,), available, preferred_suffix=suffix).natives.get(pair)
            if found:
                routes[parent] = found
                break
    return DiscoveryReport(
        account_currency, settings.account_currency, resolution, tuple(specs), routes, spec_errors
    )


_KEY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def update_env_file(path: Path, updates: dict[str, str]) -> list[str]:
    """Replace/append ONLY the given keys; every other line is preserved byte-for-byte.

    The write is atomic and keeps the original file mode (``.env`` holds secrets).
    No backup copy is created, so secrets are never duplicated on disk.
    Returns the keys whose value actually changed.
    """
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in value or "\r" in value:
            raise ValueError("invalid environment update")
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    changed: list[str] = []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        match = _KEY.match(line)
        key = match.group(1) if match else None
        if key in updates:
            if key in seen:
                continue  # Collapse duplicates; Settings would refuse them anyway.
            seen.add(key)
            ending = "\n" if line.endswith("\n") else ""
            new_line = f"{key}={updates[key]}{ending}"
            if new_line.rstrip("\n") != line.rstrip("\r\n"):
                changed.append(key)
            output.append(new_line)
        else:
            output.append(line)
    missing = [key for key in updates if key not in seen]
    if missing:
        if output and not output[-1].endswith("\n"):
            output[-1] += "\n"
        output.append("# Written by scripts.resolve_symbols (read-only broker discovery)\n")
        for key in missing:
            output.append(f"{key}={updates[key]}\n")
            changed.append(key)
    if not changed:
        return []
    mode = path.stat().st_mode & 0o777
    handle, temporary = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write("".join(output))
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return changed
