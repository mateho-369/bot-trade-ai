"""Strict local input boundaries and honest declared-coverage accounting."""

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from backtesting.contracts import BacktestOptions, LocalFile, decimal_text, utc_time
from backtesting.dataset import DatasetError, HistoricalDataset, strict_json_bytes
from tests.backtest_helpers import document, fixture_path, patch_csv, patch_manifest, rewrite


def test_hash_verified_dataset_is_immutable_and_explicitly_artificial(tmp_path):
    path = fixture_path(tmp_path)
    dataset = HistoricalDataset.load(path)
    assert dataset.manifest.origin.kind == "synthetic_fixture"
    assert dataset.coverage["unexplained_missing_m1"] == 0
    assert not dataset.coverage["tick_completeness_independently_verified"]
    assert len(dataset.dataset_sha256) == 64 and len(dataset.manifest_sha256) == 64
    with pytest.raises(TypeError):
        dataset.files["other"] = b"x"
    with pytest.raises(FrozenInstanceError):
        dataset.bars["EURUSD"][0].close = 1
    raw = dataset.files["EURUSD_M1.csv"]
    (path.parent / "EURUSD_M1.csv").write_bytes(b"mutated after load")
    assert dataset.files["EURUSD_M1.csv"] == raw  # No late file re-read/lookahead mutation.


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a.csv",
        "../a.csv",
        "a/../b.csv",
        "./a.csv",
        "C:/a.csv",
        "https://vendor/a.csv",
        "a\\b.csv",
        "a//b.csv",
        "a.csv?token=x",
        "",
    ],
)
def test_file_declarations_reject_nonlocal_cross_platform_paths(path):
    with pytest.raises(ValueError):
        LocalFile(path=path, sha256="a" * 64)


@pytest.mark.parametrize(
    "value",
    [
        1,
        0.1,
        True,
        None,
        "NaN",
        "Infinity",
        "1e-3",
        ".1",
        "01",
        " 1",
        "1 ",
        "+1",
        "-0e0",
        "9" * 50,
        "10000000000000000000",
    ],
)
def test_decimal_text_is_exact_finite_bounded_and_not_coerced(value):
    with pytest.raises(ValueError):
        decimal_text(value)


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-Infinity}',
        b'{"a":1} junk',
        b"\xff",
        b"",
    ],
)
def test_dataset_json_rejects_duplicates_constants_bad_roots_and_bytes(raw):
    with pytest.raises(DatasetError):
        strict_json_bytes(raw)


@pytest.mark.parametrize("time", ["2024-01-01", "2024-01-01T12:00:00", 1704067200, True, None, "garbage"])
def test_utc_requires_explicit_timezone(time):
    with pytest.raises(ValueError):
        utc_time(time)


def test_offsets_normalize_to_same_aware_instant():
    assert utc_time("2024-01-01T07:00:00+07:00") == utc_time("2024-01-01T00:00:00Z")


@pytest.mark.parametrize(
    "field,value",
    [
        ("format", "wrong"),
        ("quote_mode", "live"),
        ("account_currency", "usd"),
        ("replay_until", "2024-01-01T00:00:00Z"),
        ("replay_from", "2024-01-01"),
        ("bars", []),
        ("symbols", []),
        ("ticks", []),
        ("sessions", []),
    ],
)
def test_manifest_fails_closed_on_schema_bindings(tmp_path, field, value):
    path = fixture_path(tmp_path)
    patch_manifest(path, **{field: value})
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("digits", True),
        ("digits", "5"),
        ("trade_mode", True),
        ("trade_mode", 5),
        ("point", 0.00001),
        ("tick_size", "0.000015"),
        ("volume_min", "0.015"),
        ("stops_level", -1),
        ("freeze_level", False),
        ("currency_profit", "USDC_TOO_LONG"),
        ("effective_from", "2030-01-01T00:00:00Z"),
        ("profit_model", "inverse_contract"),
    ],
)
def test_historical_contracts_are_exact_reviewable_and_bounded(tmp_path, field, value):
    path = fixture_path(tmp_path)
    body = document(path)
    body["symbols"][0][field] = value
    rewrite(path, body)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


@pytest.mark.parametrize("role", ["bars", "ticks", "news"])
def test_payload_hash_failure_is_not_an_approval(tmp_path, role):
    path = fixture_path(tmp_path)
    body = document(path)
    item = body[role][0] if role in {"bars", "ticks"} else body[role]
    (path.parent / item["path"]).write_bytes(b"corruption")
    with pytest.raises(DatasetError, match="hash mismatch"):
        HistoricalDataset.load(path)


@pytest.mark.parametrize("role", ["bars", "ticks"])
def test_exact_csv_header_cannot_have_duplicates_or_extra_columns(tmp_path, role):
    path = fixture_path(tmp_path)
    body = document(path)
    target = path.parent / body[role][0]["path"]
    raw = target.read_bytes().replace(b"time,", b"time,time,", 1)
    target.write_bytes(raw)
    body[role][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    rewrite(path, body)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", "NaN"),
        ("high", "0"),
        ("low", "5"),
        ("close", "1.000001"),
        ("tick_volume", "True"),
        ("tick_volume", "1.0"),
        ("real_volume", "-1"),
        ("spread_open_points", "-1"),
        ("spread_max_points", "11"),
        ("time", "2024-01-01T00:00:01Z"),
        ("available_at", "2024-01-01T00:00:30Z"),
        ("open_available_at", "2024-01-01T00:02:00Z"),
    ],
)
def test_bar_numbers_range_grid_and_availability_reject_corruptions(tmp_path, field, value):
    path = fixture_path(tmp_path)
    patch_csv(path, change=lambda rows: rows[0].update({field: value}))
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bid", "0"),
        ("ask", "0.00001"),
        ("bid", "1.100001"),
        ("bid", "true"),
        ("time", "2024-01-01T01:00:01Z"),
        ("available_at", "2024-01-01T00:00:00Z"),
    ],
)
def test_tick_grid_spread_and_no_future_quote_guards(tmp_path, field, value):
    path = fixture_path(tmp_path)
    patch_csv(path, "ticks", lambda rows: rows[0].update({field: value}))
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


@pytest.mark.parametrize("role", ["bars", "ticks"])
def test_duplicate_or_backward_rows_never_auto_sort_an_approval(tmp_path, role):
    path = fixture_path(tmp_path)
    patch_csv(path, role, lambda rows: rows.insert(1, dict(rows[0])))
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


def test_repeated_tick_availability_cannot_hide_a_stop_touch(tmp_path):
    path = fixture_path(tmp_path)
    patch_csv(path, "ticks", lambda rows: rows[1].update(available_at=rows[0]["available_at"]))
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


def test_ohlc_requires_explicit_open_observability_and_finalized_close(tmp_path):
    path = fixture_path(tmp_path, mode="ohlc_conservative")
    patch_csv(path, change=lambda rows: rows[0].update(open_available_at=rows[0]["available_at"]))
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


def test_missing_m1_is_counted_not_filled(tmp_path):
    path = fixture_path(tmp_path)
    patch_csv(path, change=lambda rows: rows.pop(31))
    dataset = HistoricalDataset.load(path)
    assert dataset.coverage["unexplained_missing_m1"] == 1
    assert len(dataset.bars["EURUSD"]) == 61


@pytest.mark.parametrize("case", ["overlap", "backward", "nonminute", "unknown", "huge"])
def test_session_declarations_cannot_launder_coverage(tmp_path, case):
    path = fixture_path(tmp_path)
    body = document(path)
    first = body["sessions"][0]
    if case == "overlap":
        body["sessions"].append(dict(first))
    elif case == "backward":
        first["end"] = first["start"]
    elif case == "nonminute":
        first["start"] = "2024-01-01T00:00:01Z"
    elif case == "unknown":
        first["symbol"] = "UNKNOWN"
    else:
        first["end"] = "2030-01-01T00:00:00Z"
    rewrite(path, body)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


def test_symlink_files_and_parent_components_are_rejected(tmp_path):
    path = fixture_path(tmp_path)
    target = path.parent / "EURUSD_ticks.csv"
    replacement = tmp_path / "original.csv"
    target.rename(replacement)
    target.symlink_to(replacement)
    with pytest.raises(DatasetError, match="symlink"):
        HistoricalDataset.load(path)
    link = tmp_path / "linked"
    link.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(DatasetError, match="symlink"):
        HistoricalDataset.load(link / path.name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_events", True),
        ("max_events", 0),
        ("max_events", 500001),
        ("close_at_end", 1),
        ("simulate_orders", "true"),
        ("review_mode", "openai"),
    ],
)
def test_replay_options_are_explicit_actual_types(field, value):
    with pytest.raises(ValueError):
        BacktestOptions(**{field: value})


def test_total_size_and_row_budgets_fail_before_runtime(tmp_path, monkeypatch):
    import backtesting.dataset as module

    path = fixture_path(tmp_path)
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr(module, "MAX_ROWS", 10)
    with pytest.raises(DatasetError):
        HistoricalDataset.load(path)


def test_document_and_coverage_copies_cannot_silently_revise_loaded_provenance(tmp_path):
    dataset = HistoricalDataset.load(fixture_path(tmp_path))
    news = dataset.news_document
    news["snapshots"][0]["complete"] = False
    news["snapshots"].append(dict(news["snapshots"][0]))
    coverage = dataset.coverage
    coverage["unexplained_missing_m1"] = 999
    assert dataset.news_document["snapshots"][0]["complete"] is True
    assert len(dataset.news_document["snapshots"]) == 1
    assert dataset.coverage["unexplained_missing_m1"] == 0
