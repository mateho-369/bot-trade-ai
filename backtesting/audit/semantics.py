"""Recompute research metrics and connect journals/checkpoint to a captured, closed private ledger."""

from __future__ import annotations

import math
from decimal import Decimal
from zoneinfo import ZoneInfo

from backtesting.audit.contracts import (
    HASH_FIELDS,
    MAX_EQUITY_ROWS,
    canonical,
    digest,
    json_lines,
    money,
    object_json,
    stamp,
)
from backtesting.audit.inputs import inspect_inputs
from backtesting.audit.ledger import inspect_image, sql_json, sql_time
from backtesting.metrics import EquityPoint, summarize
from readiness.contracts import InspectionError


def check(condition, code):
    if not condition:
        raise InspectionError(code)


def numeric(value, low, high):
    check(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and low <= value <= high,
        "bundle_bounded_score_invalid",
    )
    return value


def inspect_semantics(bundle):
    run, report = [object_json(bundle.files[name]) for name in ("run.json", "report.json")]
    check(
        run["format"] == "reflex-replay-run-v1"
        and run["status"] == "created"
        and run["source"] == report["source"] == "historical"
        and report["format"] == "reflex-backtest-v1",
        "bundle_run_report_contract",
    )
    for name in HASH_FIELDS:
        check(run[name] == report[name] == bundle.manifest["bindings"][name], "bundle_identity_mismatch")
    check(
        object_json(bundle.files["completion.json"]) == {"status": "completed", "promotion_eligible": False},
        "bundle_completion_contract",
    )
    for name in ("promotion_eligible", "live_enabled", "auto_resume_production", "financial_history_reset"):
        check(report[name] is False, "bundle_privilege_claim_refused")
    for name in ("native_broker_calls", "provider_calls", "telegram_calls"):
        check(type(report[name]) is int and report[name] == 0, "bundle_external_execution_claim_refused")
    public = run["effective_public_config"]
    check(
        run["secrets_copied"] is False
        and public["mode"] == "backtest"
        and public["backend"] == "mock"
        and public["telegram_configured"] is False
        and public["offline_principal_only"] is True,
        "bundle_nonresearch_config_refused",
    )
    from backtesting.contracts import BacktestOptions

    options = BacktestOptions.model_validate(run["options"])
    check(
        report["review_mode"] == options.review_mode
        and report["simulated_execution_enabled"] is options.simulate_orders
        and (options.review_mode != "synthetic_research" or report["origin"]["kind"] == "synthetic_fixture"),
        "bundle_replay_options_mismatch",
    )
    policy = run["audit_policy"]
    check(
        set(policy)
        == {
            "format",
            "metric_timezone",
            "min_signal_score",
            "ai_confidence_threshold",
            "order_max_age_seconds",
            "model_filter_enabled",
            "model_min_probability",
            "primary_timeframe",
            "model_embargo_bars",
            "model_label_horizon_bars",
            "model_max_age_days",
        }
        and policy["format"] == "reflex-replay-audit-policy-v1",
        "bundle_policy_contract",
    )
    ZoneInfo(policy["metric_timezone"])
    numeric(policy["min_signal_score"], 0, 100)
    numeric(policy["ai_confidence_threshold"], 0, 100)
    numeric(policy["model_min_probability"], 0.5, 0.99)
    check(
        type(policy["model_filter_enabled"]) is bool
        and policy["model_filter_enabled"] == report["model_policy_enabled"],
        "bundle_model_policy_mismatch",
    )
    for name in ("model_embargo_bars", "model_label_horizon_bars", "model_max_age_days"):
        check(type(policy[name]) is int and 1 <= policy[name] <= 10000, "bundle_policy_bounds")
    check(
        type(policy["order_max_age_seconds"]) is int and 1 <= policy["order_max_age_seconds"] <= 3600,
        "bundle_policy_bounds",
    )
    manifest, gaps = inspect_inputs(bundle, run, report)
    start, end = manifest.replay_from, manifest.replay_until
    journals = {
        name: json_lines(bundle.files[name + ".jsonl"], limit=MAX_EQUITY_ROWS if name == "equity" else 200000)
        for name in ("equity", "trades", "signals", "operations", "ohlc_resolutions")
    }
    check(
        type(report["events"]) is int
        and 2 <= report["events"] <= 500000
        and len(journals["equity"]) == report["events"] + int(run["options"]["close_at_end"]),
        "bundle_equity_event_count",
    )
    points = []
    for row in journals["equity"]:
        check(
            set(row) == {"time", "balance", "equity", "credit", "cash_flow_total", "quotes_stale"},
            "bundle_equity_contract",
        )
        point = EquityPoint(
            stamp(row["time"]),
            *[money(row[key]) for key in ("balance", "equity", "credit", "cash_flow_total")],
            quotes_stale=row["quotes_stale"],
        )
        check(start <= point.time <= end, "bundle_journal_time_outside_replay")
        points.append(point)
    check(points and points[0].time == start and points[-1].time == end, "bundle_equity_interval_mismatch")
    trades = journals["trades"]
    identifiers = set()
    for row in trades:
        check(
            type(row["position_identifier"]) is int
            and row["position_identifier"] > 0
            and row["position_identifier"] not in identifiers
            and row["data_source"] == "historical"
            and row["direction"] in {"buy", "sell"}
            and row["status"] in {"open", "closed", "unknown"},
            "bundle_trade_identity_invalid",
        )
        identifiers.add(row["position_identifier"])
        opened = stamp(row["open_time"])
        check(start <= opened <= end, "bundle_trade_time_invalid")
        check(
            (row["close_time"] is not None) == (row["status"] == "closed"),
            "bundle_trade_status_time_mismatch",
        )
        if row["close_time"] is not None:
            check(opened <= stamp(row["close_time"]) <= end, "bundle_trade_time_invalid")
        for name in ("volume", "entry_price", "original_risk_usd", "original_target_usd"):
            check(money(row[name]) > 0, "bundle_trade_amount_invalid")
        for name in ("sl", "tp", "net_profit_account", "commission_account", "swap_account"):
            money(row[name])
        numeric(row["signal_score"], 0, 100)
        numeric(row["review_confidence"], 0, 100)
    recomputed = summarize(
        trades, points, currency=manifest.account_currency, timezone=policy["metric_timezone"], gaps=gaps
    )
    check(canonical(recomputed) == canonical(report["metrics"]), "bundle_recomputed_metrics_mismatch")
    ledger = inspect_image(bundle.files["data/replay.db"])
    check(ledger["control"]["last_config_hash"] == run["safety_config_hash"], "bundle_ledger_config_mismatch")
    compare_trades(trades, ledger["trades"], run)
    checkpoint = object_json(bundle.files["data/paper/state.json"], limit=16777216)
    check(
        set(checkpoint) == {"version", "sha256", "state"}
        and checkpoint["version"] == 1
        and checkpoint["sha256"] == digest(checkpoint["state"]),
        "bundle_checkpoint_digest_mismatch",
    )
    state = checkpoint["state"]
    check(
        state["version"] == 1
        and state["market_source_kind"] == "historical"
        and state["source_kind"] == "paper"
        and state["config_hash"] == run["safety_config_hash"]
        and state["ledger_id"].startswith("history-")
        and money(state["balance"]) == points[-1].balance,
        "bundle_checkpoint_scope_or_balance_mismatch",
    )
    # Reconciliation uses core SQL precision; compare it, do not claim extra broker precision.
    compare_deals(state["deals"], ledger["deals"], manifest.account_currency)
    for trade in trades:
        legs = [row for row in ledger["deals"] if row["position_identifier"] == trade["position_identifier"]]
        check(
            legs
            and money(trade["net_profit_account"])
            == sum(
                (
                    sum((money(row[key]) for key in ("profit", "commission", "swap", "fee")), Decimal(0))
                    for row in legs
                ),
                Decimal(0),
            )
            and money(trade["commission_account"])
            == sum((money(row["commission"]) + money(row["fee"]) for row in legs), Decimal(0))
            and money(trade["swap_account"]) == sum((money(row["swap"]) for row in legs), Decimal(0)),
            "bundle_trade_deal_economics_mismatch",
        )
    cash_after_start = sum(
        (
            sum((money(row[key]) for key in ("profit", "commission", "swap", "fee")), Decimal(0))
            for row in state["deals"]
            if stamp(row["time"]) > start
        ),
        Decimal(0),
    )
    # Decimal simulation may retain sub-1e-8 precision beyond SQL's exact eight-place ledger.
    check(
        abs(points[-1].balance - points[0].balance - cash_after_start) <= Decimal("1e-8"),
        "bundle_equity_balance_deal_flow_mismatch",
    )
    positions = {row["identifier"]: row for row in state["positions"]}
    open_trades = {row["position_identifier"]: row for row in trades if row["status"] == "open"}
    check(
        len(positions) == len(state["positions"]) and set(positions) == set(open_trades),
        "bundle_checkpoint_open_exposure_mismatch",
    )
    for identifier, position in positions.items():
        row = open_trades[identifier]
        check(
            position["symbol"] == row["symbol"]
            and position["side"] == row["direction"]
            and money(position["volume"]) == money(row["volume"])
            and money(position["sl"]) == money(row["sl"])
            and money(position["tp"]) == money(row["tp"]),
            "bundle_checkpoint_position_mismatch",
        )
    from backtesting.audit.signals import inspect_signals

    signal_observation = inspect_signals(bundle, manifest, run, report, ledger, journals)
    for row in journals["operations"]:
        check(start <= stamp(row["time"]) <= end, "bundle_journal_time_outside_replay")
        if row["operation"] == "entry" and row["status"] == "filled":
            check(
                row["signal_id"] in signal_observation["approved_ids"]
                and row["position_identifier"] in identifiers,
                "bundle_entry_signal_or_trade_missing",
            )
    resolutions = journals["ohlc_resolutions"]
    check(
        len(resolutions) == report["ohlc_resolved_exits"]
        and sum(row["reason"] == "ohlc_stop_first" for row in resolutions)
        == report["ohlc_stop_first_ambiguities"],
        "bundle_ohlc_resolution_count_mismatch",
    )
    for row in resolutions:
        check(
            manifest.quote_mode == "ohlc_conservative"
            and row["position_identifier"] in identifiers
            and stamp(row["bar_time"]) < stamp(row["resolved_at"]) <= end
            and row["exact_fill_time_known"] is False,
            "bundle_ohlc_resolution_contract",
        )
        money(row["price"])
    return {
        "captured_input_contracts_checked": True,
        "metric_fields_recomputed": len(recomputed),
        "trade_rows": len(trades),
        "equity_rows": len(points),
        "signal_rows": len(ledger["signals"]),
        "closed_private_memory_ledger_checked": True,
        "original_db_opened_by_sqlite": False,
        "private_snapshot_open_positions": len(positions),
        "stored_kill_latch_preserved": bool(ledger["control"]["kill_switch_active"]),
        **{key: value for key, value in signal_observation.items() if key != "approved_ids"},
    }


def compare_trades(journal, rows, run):
    check(len(journal) == len(rows), "bundle_ledger_trade_count_mismatch")
    direct = (
        "position_identifier",
        "symbol",
        "direction",
        "currency",
        "status",
        "profit_lock_level",
        "close_reason",
        "signal_score",
    )
    amounts = {
        "volume": "volume",
        "entry_price": "entry_price",
        "sl": "sl",
        "tp": "tp",
        "net_profit_account": "profit",
        "commission_account": "commission",
        "swap_account": "swap",
        "original_risk_usd": "initial_risk_usd",
        "original_target_usd": "target_profit_usd",
    }
    for observation, stored in zip(journal, rows, strict=True):
        check(
            all(observation[key] == stored[key] for key in direct)
            and observation["review_confidence"] == stored["ai_score"]
            and all(money(observation[key]) == money(stored[column]) for key, column in amounts.items())
            and observation["open_time"] == sql_time(stored["open_time"])
            and observation["close_time"]
            == (sql_time(stored["close_time"]) if stored["close_time"] else None)
            and stored["config_hash"] == run["safety_config_hash"],
            "bundle_ledger_trade_fields_mismatch",
        )
        features = sql_json(stored["features_json"])
        check(features["execution"]["data_source"] == "historical", "bundle_ledger_trade_source_mismatch")


def compare_deals(journal, rows, currency):
    check(len(journal) == len(rows), "bundle_checkpoint_deal_count_mismatch")
    stored = {row["ticket"]: row for row in rows}
    check(len(stored) == len(rows), "bundle_ledger_duplicate_deal")
    for row in journal:
        match = stored.get(row["ticket"])
        check(
            match is not None
            and match["currency"] == row["currency"] == currency
            and row["time"] == sql_time(match["time"])
            and all(
                row[key] == match[key]
                for key in (
                    "order_ticket",
                    "position_identifier",
                    "type",
                    "entry",
                    "symbol",
                    "magic",
                    "comment",
                )
            ),
            "bundle_checkpoint_deal_identity_mismatch",
        )
        for key in ("volume", "price", "profit", "commission", "swap", "fee"):
            scale = Decimal("1e-12") if key == "price" else Decimal("1e-8")
            check(
                money(row[key]).quantize(scale) == money(match[key]), "bundle_checkpoint_deal_money_mismatch"
            )
