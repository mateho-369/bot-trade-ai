"""Read-only, hash-verified bounded CSV/JSON. Missing bars are never forward-filled."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from backtesting.contracts import DatasetManifest, decimal_text, utc_time
from core.security import sha256_json
from trading.types import Tick

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ROWS = 500000
BAR_FIELDS = (
    "time",
    "open_available_at",
    "available_at",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread_open_points",
    "spread_max_points",
    "real_volume",
)
TICK_FIELDS = ("time", "available_at", "bid", "ask")


class DatasetError(ValueError):
    """Safe, fixed diagnostics: no raw CSV row, content or secrets."""


def strict_json_bytes(raw: bytes, *, limit=4 * 1024 * 1024):
    # Reuse the duplicate-key/nonfinite/depth/node bounded JSON parser, not permissive json.loads.
    from ai.json_validation import strict_json

    try:
        return strict_json(
            raw, max_bytes=limit, max_depth=12, max_nodes=400000, max_string=16384, max_array=50000
        )
    except Exception:
        raise DatasetError("invalid bounded strict dataset JSON") from None


def file_bytes(path: Path, *, root: Path | None = None, limit=MAX_FILE_BYTES) -> bytes:
    path = Path(path)
    if root is not None:
        root = root.resolve()
        try:
            relative = path.absolute().relative_to(root)
        except ValueError:
            raise DatasetError("dataset file escaped its root") from None
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise DatasetError("symlinked dataset component forbidden")
    elif path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise DatasetError("symlinked dataset root forbidden")
    if not path.is_file() or not 1 <= path.stat().st_size <= limit:
        raise DatasetError("missing, empty or oversized dataset file")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if not 1 <= len(raw) <= limit:
        raise DatasetError("dataset file changed beyond its size limit")
    return raw


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    time: datetime
    available_at: datetime
    open_available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int
    spread_open_points: Decimal
    spread_max_points: Decimal
    real_volume: Decimal

    @property
    def close_time(self):
        return self.time + timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Quote:
    tick: Tick
    available_at: datetime


@dataclass(frozen=True, slots=True)
class HistoricalDataset:
    manifest: DatasetManifest
    root: Path
    manifest_sha256: str
    dataset_sha256: str
    files: Mapping[str, bytes]
    bars: Mapping[str, tuple[Bar, ...]]
    quotes: Mapping[str, tuple[Quote, ...]]
    _news_json: str | None
    _review_json: str | None
    _coverage_json: str

    @property
    def news_document(self):
        return None if self._news_json is None else json.loads(self._news_json)

    @property
    def review_document(self):
        return None if self._review_json is None else json.loads(self._review_json)

    @property
    def coverage(self):
        return json.loads(self._coverage_json)

    @classmethod
    def load(cls, manifest_path: Path):
        path = Path(manifest_path).absolute()
        raw = file_bytes(path, limit=1024 * 1024)
        root = path.parent.resolve()
        try:
            manifest = DatasetManifest.model_validate(strict_json_bytes(raw, limit=1024 * 1024))
        except Exception:
            raise DatasetError("invalid historical manifest contract") from None
        files, total, records = {}, len(raw), []
        files[path.name] = raw
        declarations = (
            *manifest.bars,
            *manifest.ticks,
            *(item for item in (manifest.news, manifest.reviews) if item is not None),
            *((manifest.model.artifact, manifest.model.learning_dataset) if manifest.model else ()),
        )
        if path.name.casefold() in {item.path.casefold() for item in declarations}:
            raise DatasetError("manifest cannot also be an input payload")
        for item in declarations:
            data = file_bytes(root / item.path, root=root)
            total += len(data)
            digest = hashlib.sha256(data).hexdigest()
            if total > MAX_TOTAL_BYTES or digest != item.sha256:
                raise DatasetError("dataset total-size or input-hash mismatch")
            files[item.path] = data
            records.append({"path": item.path, "sha256": digest, "bytes": len(data)})
        bars, quotes, rows = {}, {}, 0
        specs = {item.name: item for item in manifest.symbols}
        for item in manifest.bars:
            result = []
            for row in csv_rows(files[item.path], BAR_FIELDS):
                try:
                    bar = parse_bar(item.symbol, row)
                    if bar.time < specs[item.symbol].effective_from:
                        raise ValueError
                    if any(
                        value % specs[item.symbol].tick_size
                        for value in (bar.open, bar.high, bar.low, bar.close)
                    ):
                        raise ValueError
                    if result and (bar.time <= result[-1].time or bar.available_at < result[-1].available_at):
                        raise ValueError
                    if manifest.quote_mode == "ohlc_conservative" and (
                        bar.available_at != bar.close_time or bar.open_available_at != bar.time
                    ):
                        raise ValueError
                    if not in_session(manifest, item.symbol, bar.time):
                        raise ValueError
                    result.append(bar)
                    rows += 1
                    if rows > MAX_ROWS:
                        raise ValueError
                except Exception:
                    raise DatasetError(
                        "invalid M1 price/grid/availability/session/chronology/row limit"
                    ) from None
            if not result:
                raise DatasetError("empty base-bar history")
            bars[item.symbol] = tuple(result)
        for item in manifest.ticks:
            result = []
            for row in csv_rows(files[item.path], TICK_FIELDS):
                try:
                    time, available = utc_time(row["time"]), utc_time(row["available_at"])
                    bid, ask = decimal_text(row["bid"]), decimal_text(row["ask"])
                    tick = Tick(item.symbol, bid, ask, time)
                    if time > available or time < specs[item.symbol].effective_from:
                        raise ValueError
                    if bid % specs[item.symbol].tick_size or ask % specs[item.symbol].tick_size:
                        raise ValueError
                    if result and (time <= result[-1].tick.time or available <= result[-1].available_at):
                        raise ValueError
                    if not in_session(manifest, item.symbol, time):
                        raise ValueError
                    result.append(Quote(tick, available))
                    rows += 1
                    if rows > MAX_ROWS:
                        raise ValueError
                except Exception:
                    raise DatasetError(
                        "invalid tick/grid/availability/session/chronology/row limit"
                    ) from None
            if not result:
                raise DatasetError("empty tick history")
            quotes[item.symbol] = tuple(result)
        documents = []
        for item in (manifest.news, manifest.reviews):
            documents.append(
                None
                if item is None
                else json.dumps(strict_json_bytes(files[item.path]), allow_nan=False, separators=(",", ":"))
            )
        digest = hashlib.sha256(raw).hexdigest()
        coverage = coverage_summary(manifest, bars)
        return cls(
            manifest,
            root,
            digest,
            sha256_json({"manifest_sha256": digest, "files": sorted(records, key=lambda r: r["path"])}),
            MappingProxyType(files),
            MappingProxyType(bars),
            MappingProxyType(quotes),
            *documents,
            json.dumps(coverage, allow_nan=False, separators=(",", ":")),
        )


def csv_rows(raw: bytes, fields):
    try:
        text = raw.decode("utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError
        for row in reader:
            if set(row) != set(fields) or any(value is None or len(value) > 64 for value in row.values()):
                raise ValueError
            yield row
    except (ValueError, UnicodeError, csv.Error):
        raise DatasetError("invalid exact UTF-8 CSV schema") from None


def parse_bar(symbol, row):
    time, available = utc_time(row["time"]), utc_time(row["available_at"])
    opening_at = utc_time(row["open_available_at"])
    if not time <= opening_at <= available:
        raise ValueError
    if time.second or time.microsecond or available < time + timedelta(minutes=1):
        raise ValueError
    numbers = [decimal_text(row[name]) for name in ("open", "high", "low", "close")]
    opening, high, low, close = numbers
    volume = row["tick_volume"]
    if not volume.isascii() or not volume.isdecimal() or not 0 <= int(volume) <= 10**12:
        raise ValueError
    spread_open, spread_max, real = [
        decimal_text(row[name]) for name in ("spread_open_points", "spread_max_points", "real_volume")
    ]
    if min(numbers) <= 0 or low > min(opening, close) or high < max(opening, close, low):
        raise ValueError
    if spread_open < 0 or spread_max < spread_open or real < 0:
        raise ValueError
    return Bar(symbol, time, available, opening_at, *numbers, int(volume), spread_open, spread_max, real)


def in_session(manifest, symbol, when):
    return any(item.symbol == symbol and item.start <= when < item.end for item in manifest.sessions)


def coverage_summary(manifest, bars):
    details, total = {}, 0
    for spec in manifest.symbols:
        actual = {bar.time for bar in bars[spec.name]}
        expected, missing = 0, 0
        # Bound scanning even if the caller declares centuries of nonexistent market hours.
        for session in manifest.sessions:
            if session.symbol != spec.name:
                continue
            minutes = int((session.end - session.start).total_seconds() // 60)
            expected += minutes
            if expected > MAX_ROWS:
                raise DatasetError("declared session coverage exceeds row bound")
            for index in range(minutes):
                if session.start + timedelta(minutes=index) not in actual:
                    missing += 1
        details[spec.name] = {"expected_m1": expected, "observed_m1": len(actual), "missing_m1": missing}
        total += missing
    return {
        "unexplained_missing_m1": total,
        "by_symbol": details,
        "session_calendar_authenticity_verified": False,
        "tick_completeness_independently_verified": False,
    }
