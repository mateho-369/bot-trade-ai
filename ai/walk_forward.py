"""Expanding chronological validation; event purging, label-availability cutoff and time embargo."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ai.dataset import LearningDataset
from core.settings import TIMEFRAME_MINUTES, Settings
from trading.types import BrokerError


@dataclass(frozen=True, slots=True)
class PurgedFold:
    number: int
    train: tuple[int, ...]
    test: tuple[int, ...]
    fit_cutoff: datetime
    test_start: datetime
    test_end: datetime
    purged: int


def purged_walk_forward(dataset: LearningDataset, settings: Settings) -> tuple[PurgedFold, ...]:
    rows = dataset.samples
    if not settings.model_min_labelled_trades <= len(rows) <= settings.model_max_dataset_rows:
        raise BrokerError("insufficient/oversized labelled dataset")
    times = sorted({s.decision_at for s in rows})
    folds = settings.model_walk_forward_folds
    initial = max(30, len(times) * 2 // 5)
    remaining = len(times) - initial
    if remaining < folds * 10:
        raise BrokerError("insufficient distinct chronology for fixed walk-forward folds")
    # Split by timestamps, never within simultaneous multi-symbol observations.
    boundaries = [initial + remaining * i // folds for i in range(folds + 1)]
    period = TIMEFRAME_MINUTES[settings.primary_timeframe] * 60
    embargo = timedelta(seconds=period * settings.model_embargo_bars)
    horizon = timedelta(seconds=period * settings.model_label_horizon_bars)
    result = []
    for number in range(folds):
        start = times[boundaries[number]]
        end = (
            times[boundaries[number + 1]]
            if boundaries[number + 1] < len(times)
            else times[-1] + timedelta(microseconds=1)
        )
        cutoff = start - embargo
        prior = tuple(i for i, s in enumerate(rows) if s.decision_at < start)
        train = tuple(
            i
            for i in prior
            if rows[i].decision_at < cutoff
            and rows[i].label_available_at < cutoff
            and max(rows[i].exit_at, rows[i].decision_at + horizon) < start
        )
        test = tuple(i for i, s in enumerate(rows) if start <= s.decision_at < end)
        if len(train) < 20 or len(test) < 10 or {rows[i].label for i in train} != {0, 1}:
            raise BrokerError("purged fold lacks chronology/classes; do not replace it with shuffled CV")
        result.append(PurgedFold(number, train, test, cutoff, start, end, len(prior) - len(train)))
    return tuple(result)
