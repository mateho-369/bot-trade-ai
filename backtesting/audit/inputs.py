"""Check captured input hash/chronology/grid/session contracts without reloading source paths or a broker."""

from __future__ import annotations

from datetime import timedelta

from backtesting.audit.contracts import REQUIRED_FILES, digest, object_json, sha
from backtesting.contracts import DatasetManifest, decimal_text, utc_time
from backtesting.dataset import (
    BAR_FIELDS,
    MAX_ROWS,
    TICK_FIELDS,
    coverage_summary,
    csv_rows,
    in_session,
    parse_bar,
)
from readiness.contracts import InspectionError
from readiness.files import relative_name


def inspect_inputs(bundle, run, report):
    try:
        name = relative_name(run["input_manifest"])
        raw = bundle.files["inputs/" + name]
        manifest = DatasetManifest.model_validate(object_json(raw, limit=1048576))
        declarations = [
            *manifest.bars,
            *manifest.ticks,
            *[item for item in (manifest.news, manifest.reviews) if item is not None],
        ]
        if manifest.model:
            declarations.extend([manifest.model.artifact, manifest.model.learning_dataset])
        expected = {"inputs/" + name} | {"inputs/" + item.path for item in declarations}
        if {path for path in bundle.files if path.startswith("inputs/")} != expected:
            raise ValueError
        models = {"data/models/" + manifest.model.artifact.sha256 + ".json"} if manifest.model else set()
        if set(bundle.files) != REQUIRED_FILES | expected | models:
            raise ValueError
        records = []
        for item in declarations:
            data = bundle.files["inputs/" + item.path]
            if sha(data) != item.sha256:
                raise ValueError
            records.append({"path": item.path, "sha256": item.sha256, "bytes": len(data)})
        manifest_sha = sha(raw)
        if (
            manifest_sha != report["manifest_sha256"]
            or digest(
                {"manifest_sha256": manifest_sha, "files": sorted(records, key=lambda row: row["path"])}
            )
            != run["dataset_sha256"]
            or report["origin"] != manifest.origin.model_dump(mode="json")
            or report["quote_mode"] != manifest.quote_mode
            or utc_time(report["started_at"]) != manifest.replay_from
            or utc_time(report["finished_at"]) != manifest.replay_until
            or manifest.account_currency != report["metrics"]["currency"]
        ):
            raise ValueError
        bars, specs, count = {}, {item.name: item for item in manifest.symbols}, 0
        for declaration in manifest.bars:
            rows, previous = [], None
            for row in csv_rows(bundle.files["inputs/" + declaration.path], BAR_FIELDS):
                bar = parse_bar(declaration.symbol, row)
                spec = specs[declaration.symbol]
                if (
                    bar.time < spec.effective_from
                    or not in_session(manifest, bar.symbol, bar.time)
                    or any(value % spec.tick_size for value in (bar.open, bar.high, bar.low, bar.close))
                    or previous
                    and (bar.time <= previous.time or bar.available_at < previous.available_at)
                    or manifest.quote_mode == "ohlc_conservative"
                    and (bar.available_at != bar.close_time or bar.open_available_at != bar.time)
                ):
                    raise ValueError
                rows.append(bar)
                previous = bar
                count += 1
                if count > MAX_ROWS:
                    raise ValueError
            if not rows:
                raise ValueError
            bars[declaration.symbol] = tuple(rows)
        for declaration in manifest.ticks:
            previous, rows = None, 0
            spec = specs[declaration.symbol]
            for row in csv_rows(bundle.files["inputs/" + declaration.path], TICK_FIELDS):
                when, available = utc_time(row["time"]), utc_time(row["available_at"])
                bid, ask = decimal_text(row["bid"]), decimal_text(row["ask"])
                if (
                    when > available
                    or when < spec.effective_from
                    or min(bid, ask) <= 0
                    or ask < bid
                    or bid % spec.tick_size
                    or ask % spec.tick_size
                    or not in_session(manifest, spec.name, when)
                    or previous
                    and (when <= previous[0] or available <= previous[1])
                ):
                    raise ValueError
                previous, rows, count = (when, available), rows + 1, count + 1
                if count > MAX_ROWS:
                    raise ValueError
            if not rows:
                raise ValueError
        coverage = coverage_summary(manifest, bars)
        if coverage != report["archive_coverage"]:
            raise ValueError
        gaps = 0
        for session in manifest.sessions:
            start, end = max(session.start, manifest.replay_from), min(session.end, manifest.replay_until)
            if end <= start:
                continue
            actual = {bar.time for bar in bars[session.symbol]}
            first = max(session.start, start.replace(second=0, microsecond=0))
            gaps += sum(
                first + timedelta(minutes=index) not in actual
                for index in range(max(0, int((end - first).total_seconds() // 60)))
            )
        return manifest, gaps
    except Exception:
        raise InspectionError("bundle_captured_input_contract_mismatch") from None
