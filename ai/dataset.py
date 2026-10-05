"""Immutable selected-trade labels. No labels from open trades or invented price replay."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

import numpy as np

from ai.feature_engineering import FEATURE_NAMES, FEATURE_SCHEMA_HASH, FEATURE_VERSION, FeatureVector
from ai.json_validation import AIInvalidResponse, strict_json
from core.security import canonical_json, sha256_json
from core.settings import Settings
from trading.types import BrokerError, SourceKind, aware_utc, valid_key

DATASET_VERSION = "reflex-learning-dataset-v1"


@dataclass(frozen=True, slots=True)
class TradeSample:
    sample_id: str
    decision_at: datetime
    entry_at: datetime
    exit_at: datetime
    label_available_at: datetime
    symbol: str
    direction: str
    features: FeatureVector
    net_usd: Decimal
    risk_usd: Decimal
    proof_hash: str

    def __post_init__(self):
        valid_key(self.sample_id)
        valid_key(self.proof_hash)
        times = tuple(
            aware_utc(t) for t in (self.decision_at, self.entry_at, self.exit_at, self.label_available_at)
        )
        if not times[0] <= times[1] < times[2] <= times[3]:
            raise BrokerError("label/feature availability violates event chronology")
        if (
            not isinstance(self.features, FeatureVector)
            or self.direction not in {"buy", "sell"}
            or not isinstance(self.symbol, str)
            or not 1 <= len(self.symbol) <= 64
            or any(ord(c) < 32 for c in self.symbol)
        ):
            raise BrokerError("invalid sample contract")
        for value in (self.net_usd, self.risk_usd):
            if not isinstance(value, Decimal) or not value.is_finite() or abs(value) > Decimal("1000000000"):
                raise BrokerError("sample money requires finite bounded Decimal")
        if self.risk_usd <= 0 or abs(self.net_usd / self.risk_usd) > Decimal("1000000"):
            raise BrokerError("sample lacks a legal authorization-risk denominator")
        if self.features.values[0] != (1 if self.direction == "buy" else -1):
            raise BrokerError("sample side differs from its pre-entry vector")

    @property
    def label(self):
        return int(self.net_usd > 0)  # Breakeven is NOT a profitable label.

    @property
    def net_r(self):
        # Risk was an authorization estimate, not a guaranteed maximum loss.
        return float(self.net_usd / self.risk_usd)

    def to_dict(self):
        data = json.loads(canonical_json(asdict(self)))
        data["features"] = self.features.to_dict()
        return data

    @classmethod
    def from_dict(cls, values):
        try:
            data = dict(values)
            for key in ("decision_at", "entry_at", "exit_at", "label_available_at"):
                data[key] = datetime.fromisoformat(data[key])
            for key in ("net_usd", "risk_usd"):
                if not isinstance(data[key], str):
                    raise ValueError
                data[key] = Decimal(data[key])
            data["features"] = FeatureVector.from_dict(data["features"])
            return cls(**data)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise BrokerError("invalid learning sample; raw payload suppressed") from None


@dataclass(frozen=True, slots=True)
class LearningDataset:
    source: SourceKind
    origin: Literal["reconciled_trades", "fixture"]
    policy_hash: str
    feature_code_hash: str
    account_scope_hash: str
    exported_at: datetime
    samples: tuple[TradeSample, ...]

    def __post_init__(self):
        if not isinstance(self.source, SourceKind) or self.origin not in {"reconciled_trades", "fixture"}:
            raise BrokerError("invalid dataset provenance")
        for key in (self.policy_hash, self.feature_code_hash, self.account_scope_hash):
            valid_key(key)
        now = aware_utc(self.exported_at)
        if not isinstance(self.samples, tuple) or not 1 <= len(self.samples) <= 100000:
            raise BrokerError("bounded nonempty sample tuple required")
        if any(not isinstance(s, TradeSample) or s.label_available_at > now for s in self.samples):
            raise BrokerError("future/incomplete dataset label")
        ids = [s.sample_id for s in self.samples]
        if len(set(ids)) != len(ids):
            raise BrokerError("duplicate learning event")
        if list(self.samples) != sorted(self.samples, key=lambda s: (s.decision_at, s.sample_id)):
            raise BrokerError("dataset must be deterministically time ordered")

    @property
    def digest(self):
        return sha256_json(self.to_dict())

    @property
    def trained_through(self):
        return max(s.label_available_at for s in self.samples)

    def matrices(self):
        return (
            np.asarray([s.features.values for s in self.samples], dtype=np.float64),
            np.asarray([s.label for s in self.samples], dtype=np.int64),
        )

    def to_dict(self):
        return {
            "format": DATASET_VERSION,
            "feature_version": FEATURE_VERSION,
            "feature_schema_hash": FEATURE_SCHEMA_HASH,
            "feature_names": list(FEATURE_NAMES),
            "source": self.source.value,
            "origin": self.origin,
            "policy_hash": self.policy_hash,
            "feature_code_hash": self.feature_code_hash,
            "account_scope_hash": self.account_scope_hash,
            "exported_at": self.exported_at.isoformat(),
            "selection": "executed_trades_only",
            "costs_included": True,
            "currency": "USD",
            "samples": [s.to_dict() for s in self.samples],
        }

    @classmethod
    def from_dict(cls, values: dict, settings: Settings):
        try:
            header = {
                "format": DATASET_VERSION,
                "feature_version": FEATURE_VERSION,
                "feature_schema_hash": FEATURE_SCHEMA_HASH,
                "feature_names": list(FEATURE_NAMES),
                "selection": "executed_trades_only",
                "costs_included": True,
                "currency": "USD",
            }
            if set(values) != set(header) | {
                "source",
                "origin",
                "policy_hash",
                "feature_code_hash",
                "account_scope_hash",
                "exported_at",
                "samples",
            }:
                raise ValueError
            if values.get("costs_included") is not True or any(values.get(k) != v for k, v in header.items()):
                raise ValueError
            rows = values["samples"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= settings.model_max_dataset_rows:
                raise ValueError
            return cls(
                SourceKind(values["source"]),
                values["origin"],
                values["policy_hash"],
                values["feature_code_hash"],
                values["account_scope_hash"],
                datetime.fromisoformat(values["exported_at"]),
                tuple(TradeSample.from_dict(s) for s in rows),
            )
        except (ValueError, TypeError, KeyError, ArithmeticError):
            raise BrokerError("invalid learning dataset") from None

    @classmethod
    def from_json(cls, raw: bytes, settings: Settings):
        try:
            data = strict_json(
                raw,
                max_bytes=67108864,
                max_depth=12,
                max_nodes=settings.model_max_dataset_rows * 64,
                max_string=1000,
                max_array=settings.model_max_dataset_rows,
            )
            return cls.from_dict(data, settings)
        except AIInvalidResponse:
            raise BrokerError("invalid learning dataset JSON") from None
