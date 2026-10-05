"""Research proposal/review/context bindings and private row references, never permission issuance."""

from __future__ import annotations

from collections import Counter

from backtesting.audit.contracts import digest, object_json, stamp
from backtesting.audit.ledger import sql_json, sql_time
from backtesting.audit.model import ModelObservation
from backtesting.audit.semantics import check, numeric
from strategy.base_strategy import AIEntryReview
from trading.risk_types import DecisionContext

IMMUTABLE = (
    "signal_format",
    "source",
    "logical_symbol",
    "symbol",
    "mode",
    "strategy",
    "timeframe",
    "observed_at",
    "bar_time",
    "bar_closed_at",
    "config_hash",
    "strategy_config_hash",
    "code_hash",
    "model_sha256",
    "history_hash",
    "symbol_info_hash",
    "stop_price",
    "atr",
    "bar_close_price",
    "feature_snapshot",
    "technical",
)


def inspect_signals(bundle, manifest, run, report, ledger, journals):
    policy, latest, approved, reasons = run["audit_policy"], {}, set(), Counter()
    model = ModelObservation(bundle, manifest, run, ledger)
    from backtesting.reviews import ReviewArchive

    archive = (
        ReviewArchive.model_validate(object_json(bundle.files["inputs/" + manifest.reviews.path]))
        if manifest.reviews
        else None
    )
    archived = {item.proposal_hash: item for item in archive.entries} if archive else {}
    from backtesting.news import NewsArchive

    news_archive = (
        NewsArchive.model_validate(object_json(bundle.files["inputs/" + manifest.news.path]))
        if manifest.news
        else None
    )
    previous = None
    for trace in journals["signals"]:
        when = stamp(trace["time"])
        check(
            manifest.replay_from <= when <= manifest.replay_until and (previous is None or when >= previous),
            "bundle_signal_trace_chronology",
        )
        previous = when
        check(
            trace["source"] == "historical"
            and trace["phase"] in {"analysis", "finalization", "end_pending_veto"},
            "bundle_signal_trace_contract",
        )
        numeric(trace["score"], 0, 100)
        if (trace["phase"] == "analysis" and trace["state"] != "pending") or (
            trace["phase"] != "analysis" and trace["state"] != "approved"
        ):
            # Reasons are bounded internal codes in SignalResult; fixed operation errors are accounted below.
            reasons.update(trace["reasons"])
        identifier = trace["signal_id"]
        if identifier is None:
            check(not trace["payload"] and trace["state"] != "approved", "bundle_ephemeral_signal_claim")
            continue
        check(type(identifier) is int and identifier > 0, "bundle_signal_id_invalid")
        payload = trace["payload"]
        check(
            set(IMMUTABLE).issubset(payload)
            and digest({key: payload[key] for key in IMMUTABLE})
            == trace["proposal_hash"]
            == payload["proposal_hash"],
            "bundle_signal_proposal_digest_mismatch",
        )
        check(
            payload["signal_format"] == "reflex-signal-v1"
            and payload["source"] == "historical"
            and payload["mode"] == "backtest"
            and payload["symbol"] == trace["symbol"]
            and payload["technical"]["score"] == trace["score"]
            and payload["strategy"] == "weighted_router_v1"
            and payload["config_hash"] == run["safety_config_hash"]
            and payload["strategy_config_hash"] == run["strategy_config_hash"]
            and payload["code_hash"] == run["code_hash"]
            and payload["model_sha256"] == run["model_sha256"],
            "bundle_signal_runtime_binding_mismatch",
        )
        observed, close = stamp(payload["observed_at"]), stamp(payload["bar_closed_at"])
        check(
            manifest.replay_from <= observed <= when and stamp(payload["bar_time"]) < close <= observed,
            "bundle_signal_future_snapshot",
        )
        frames = payload["feature_snapshot"]["frames"]
        check(
            len(frames) == 3
            and all(
                stamp(frame["bar_time"]) < stamp(frame["closed_at"]) <= close
                and type(frame["bars"]) is int
                and frame["bars"] >= 200
                for frame in frames
            ),
            "bundle_signal_future_or_incomplete_frame",
        )
        is_approved = trace["state"] == "approved"
        model.gate(payload, when=when, approved=is_approved, policy=policy)
        if is_approved:
            check(
                trace["phase"] in {"finalization", "end_pending_veto"} or identifier in approved,
                "bundle_analysis_approval_without_review",
            )
            context = DecisionContext.from_dict(payload["decision_context"])
            review = AIEntryReview.from_dict(payload["ai_review"])
            check(
                context.digest == payload["decision_digest"]
                and context.signal_id == identifier
                and context.observed_at == observed
                and context.bar_closed_at == close
                and context.source.value == review.source.value == "historical"
                and context.signal_score == trace["score"] >= policy["min_signal_score"]
                and context.ai_confidence == review.confidence >= policy["ai_confidence_threshold"]
                and review.decision == "approve"
                and review.provider == "replay"
                and review.proposal_hash == payload["proposal_hash"]
                and review.code_hash == run["code_hash"]
                and review.model_sha256 == run["model_sha256"]
                and review.news_hash == context.news.evidence_hash == payload["news_hash"]
                and context.features["review_digest"] == review.digest
                and context.features["proposal_hash"] == payload["proposal_hash"]
                and context.features["history_hash"] == payload["history_hash"]
                and context.features["feature_snapshot"] == payload["feature_snapshot"]
                and context.features["technical"] == payload["technical"]
                and 0 <= (when - review.observed_at).total_seconds() <= policy["order_max_age_seconds"],
                "bundle_approved_context_or_review_mismatch",
            )
            if run["options"]["review_mode"] == "archive":
                entry = archived.get(payload["proposal_hash"])
                check(
                    entry is not None
                    and entry.available_at <= when
                    and entry.observed_at == review.observed_at
                    and entry.confidence == review.confidence
                    and entry.code_hash == review.code_hash
                    and entry.model_sha256 == review.model_sha256
                    and entry.news_hash == review.news_hash
                    and entry.request_hash == review.request_hash
                    and entry.provider_model == review.provider_model
                    and entry.risk_percent == review.risk_percent
                    and entry.decision == review.decision,
                    "bundle_archived_review_availability_mismatch",
                )
            else:
                check(
                    run["options"]["review_mode"] == "synthetic_research"
                    and manifest.origin.kind == "synthetic_fixture"
                    and review.provider_model == "artificial-fixture-not-ai"
                    and review.request_hash == digest({"synthetic_research_only": payload["proposal_hash"]}),
                    "bundle_artificial_review_scope_mismatch",
                )
            news = context.news
            snapshots = (
                [item for item in news_archive.snapshots if item.available_at <= when] if news_archive else []
            )
            snapshot = snapshots[-1] if snapshots else None
            check(
                snapshot is not None
                and news.known
                and news.safe
                and not news.managed
                and snapshot.complete
                and payload["logical_symbol"] in snapshot.symbols
                and news.headlines_fetched_at == snapshot.headlines_fetched_at <= when
                and news.calendar_fetched_at == snapshot.calendar_fetched_at <= when
                and news.calendar_covered_from == snapshot.covered_from <= when
                and news.calendar_covered_until == snapshot.covered_until >= when
                and news.evidence_hash
                == digest(
                    {
                        "format": "reflex-replay-news-window-v1",
                        "symbol": payload["logical_symbol"],
                        "snapshot": digest(snapshot.model_dump(mode="json")),
                        "policy": run["strategy_config_hash"],
                    }
                ),
                "bundle_approved_news_window_mismatch",
            )
            if policy["model_filter_enabled"]:
                check(
                    context.features["model_gate"] == payload["model_gate"],
                    "bundle_context_model_gate_mismatch",
                )
            approved.add(identifier)
        latest[identifier] = trace
    check(set(latest) == {row["id"] for row in ledger["signals"]}, "bundle_ledger_signal_set_mismatch")
    for row in ledger["signals"]:
        trace = latest[row["id"]]
        payload = trace["payload"]
        check(
            sql_json(row["features_json"]) == payload
            and row["mode"] == "backtest"
            and row["symbol"] == trace["symbol"]
            and row["score"] == trace["score"]
            and row["final_decision"] == trace["state"]
            and sql_time(row["time"]) == payload["observed_at"]
            and sql_time(row["bar_time"]) == payload["bar_time"]
            and row["strategy"] == payload["strategy"]
            and row["timeframe"] == payload["timeframe"]
            and row["direction"] == payload["technical"]["direction"]
            and row["config_hash"] == run["safety_config_hash"],
            "bundle_ledger_signal_fields_mismatch",
        )
        check(
            (row["reason"].split(",") if row["reason"] else []) == trace["reasons"],
            "bundle_signal_reason_mismatch",
        )
        if trace["state"] == "approved":
            check(row["ai_score"] == payload["ai_review"]["confidence"], "bundle_signal_confidence_mismatch")
    intents = {row["id"]: row for row in ledger["intents"]}
    for trade in ledger["trades"]:
        check(trade["order_intent_id"] in intents, "bundle_trade_intent_missing")
        features = sql_json(trade["features_json"])
        identifier = features["decision"]["signal_id"]
        check(identifier in approved, "bundle_trade_without_approved_signal")
        intent = intents[trade["order_intent_id"]]
        check(
            intent["config_hash"] == run["safety_config_hash"]
            and intent["state"] == "reconciled"
            and intent["symbol"] == trade["symbol"]
            and intent["direction"] == trade["direction"],
            "bundle_trade_entry_intent_mismatch",
        )
    for row in journals["operations"]:
        if row["operation"] == "entry" and row["status"] == "veto":
            if "error_kind" in row:
                reasons.update(["execution_" + row["error_kind"]])
            elif "reason" in row:
                reasons.update([row["reason"]])
        elif row["operation"] == "end_close" and row["status"] == "veto":
            reasons.update(["end_close_" + row["error_kind"]])
    declared = report["veto_reasons"]
    check(all(type(value) is int and value > 0 for value in declared.values()), "bundle_veto_count_invalid")
    check(
        all(declared.get(key) == value for key, value in reasons.items()) and set(declared) == set(reasons),
        "bundle_report_veto_counts_mismatch",
    )
    return {
        "approved_ids": approved,
        "proposal_review_context_bindings_checked": True,
        "logistic_probability_observations_recomputed": model.checked_logistic,
        "lightgbm_probability_observations_not_reexecuted": model.not_reexecuted_lightgbm,
        "training_reconstruction_reexecuted": False,
    }
