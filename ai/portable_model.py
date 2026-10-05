"""Bounded portable model formats: numeric logistic JSON or owner-trained LightGBM native text."""

from __future__ import annotations

import math
import re

import numpy as np

from ai.feature_engineering import FEATURE_NAMES
from trading.types import BrokerError


def _numbers(values, *, positive=False):
    if not isinstance(values, list) or len(values) != len(FEATURE_NAMES):
        raise BrokerError("invalid fixed-size model vector")
    if any(
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or not math.isfinite(x)
        or abs(x) > 1e6
        or positive
        and x <= 0
        for x in values
    ):
        raise BrokerError("invalid bounded model numbers")
    return np.asarray(values, dtype=np.float64)


class PortableModel:
    def __init__(self, payload: dict):
        self.algorithm = payload.get("algorithm")
        self._booster = None
        if self.algorithm == "logistic":
            if set(payload) != {"algorithm", "mean", "scale", "coefficients", "intercept"}:
                raise BrokerError("unexpected logistic model fields")
            self.mean, self.scale, self.coefficients = (
                _numbers(payload[k], positive=k == "scale") for k in ("mean", "scale", "coefficients")
            )
            self.intercept = payload["intercept"]
            if (
                isinstance(self.intercept, bool)
                or not isinstance(self.intercept, (int, float))
                or not math.isfinite(self.intercept)
                or abs(self.intercept) > 1e6
            ):
                raise BrokerError("invalid logistic intercept")
        elif self.algorithm == "lightgbm":
            if set(payload) != {"algorithm", "model_text", "library_version"}:
                raise BrokerError("unexpected LightGBM model fields")
            text = payload["model_text"]
            if (
                not isinstance(text, str)
                or not 1 <= len(text.encode()) <= 1048576
                or "\0" in text
                or not text.startswith("tree\n")
                or not 1 <= text.count("\nTree=") <= 200
                or f"max_feature_idx={len(FEATURE_NAMES) - 1}\n" not in text
                or "feature_names=" + " ".join(FEATURE_NAMES) + "\n" not in text
                or not re.search(r"^objective=binary(?:\s|$)", text, re.M)
            ):
                raise BrokerError("unsupported/unbounded LightGBM text")
            leaves = re.findall(r"^num_leaves=(\d+)$", text, re.M)
            if any(not 1 <= int(n) <= 31 for n in leaves) or len(leaves) != text.count("\nTree="):
                raise BrokerError("unbounded LightGBM tree")
            import lightgbm as lgb

            if payload["library_version"] != lgb.__version__:
                raise BrokerError("LightGBM version mismatch; revalidate under matching release")
            self._booster = lgb.Booster(model_str=text)
            if self._booster.num_feature() != len(FEATURE_NAMES):
                raise BrokerError("LightGBM schema mismatch")
        else:
            raise BrokerError("unsupported portable model algorithm")

    def predict(self, matrix) -> np.ndarray:
        x = np.asarray(matrix, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != len(FEATURE_NAMES) or x.shape[0] > 100000 or not np.isfinite(x).all():
            raise BrokerError("invalid inference matrix")
        if self.algorithm == "logistic":
            z = (x - self.mean) / self.scale @ self.coefficients + self.intercept
            result = 1 / (1 + np.exp(-np.clip(z, -700, 700)))
        else:
            result = np.asarray(self._booster.predict(x, num_threads=1), dtype=np.float64)
        if (
            result.shape != (len(x),)
            or not np.isfinite(result).all()
            or (result < 0).any()
            or (result > 1).any()
        ):
            raise BrokerError("invalid model prediction")
        return result
