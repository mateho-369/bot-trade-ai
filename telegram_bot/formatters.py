"""Bounded plain-text replies; model/headline text is never HTML/Markdown."""

HELP = """MT5 AI ReflexBot · owner only
/status /dashboard — stored observations, not broker polls
/positions /trades /signals /news /suggestions /settings /logs
/ai — AI decision journal (decisions, trailing, lessons, adjustments)
/pause — stop NEW entries; protective logic continues
/kill — latch NEW entries off; does not flatten
/resume — fresh single-use confirmation, not live approval
/close TICKET POSITION_IDENTIFIER — exact bot-owned capture
/close_all — confirm captured owned positions, pause entries
/approve SUGGESTION_ID — approve ONLY, never apply or trade
/reject SUGGESTION_ID — one-way decision (also rejects major AI config changes)
/ai_reset — revert every AI config adjustment to your settings
/ai_fallback_status — AI fallback mode, AI health, dynamic limits
/ai_fallback_block — AI down => NO new entries (default, safest)
/ai_fallback_technical — AI down => technical score >= threshold only
/limits — AI-dynamic limits, owner defaults and hard caps
/alerts [critical|error|warning|info] — last alerts (Alert Center)
/ack_all — acknowledge every alert · /ack ALERT_ID — one alert
/ai_stats — per-AI approvals, rejections, trades, win rate, net profit, failures
/audit — check every trade has a matching valid AI approval

No order/open/live/risk-escalation/withdrawal commands.
Reopen expired Mini App sessions from this private owner bot.
A pause/kill cannot recall an already submitted SDK write."""


def render(section, result):
    if section == "dashboard":
        runtime, kpis = result["runtime"], result["kpis"]
        currency = kpis["currency"]

        def money(key):
            return kpis[key] if kpis.get(key) is not None else "unknown"

        return (
            "MT5 AI ReflexBot · stored status\n"
            f"Mode: {result['meta']['mode']} | entries: {runtime['desired_state']}\n"
            f"Kill latch: {runtime['kill_switch_active']} | current lease: {runtime['lease_current']}\n"
            f"Balance/equity ({currency}): {money('balance')} / {money('equity')}\n"
            f"Realized today ({currency}): {money('realized_today_account')}\n"
            f"Open/unknown: {kpis['open_count']} / {kpis['unknown_count']}\n"
            f"Observation current: {kpis['observation_current']}\n"
            "This status is NOT permission to trade or a real-time floating P&L."
        )
    if section == "settings":
        values = result["values"]
        return (
            "READ-ONLY settings\n"
            + "\n".join(f"{k}: {v}" for k, v in values.items())
            + "\n"
            + result["policy"]
        )
    if section == "ai_fallback":
        ai, limits = result.get("ai", {}), result.get("limits", {})
        return (
            f"AI fallback mode: {result['mode']}\n"
            f"{result.get('description', '')}\n"
            f"AI status: {'available' if ai.get('available') else 'UNAVAILABLE'} "
            f"({ai.get('mode', 'unknown')})\n"
            f"Active limits ({limits.get('source', '?')}): {limits.get('max_daily_trades')} trades/day, "
            f"{limits.get('max_open_positions')} positions, risk {limits.get('risk_percent')}%, "
            f"target {limits.get('target_usd')}\n"
            + (
                "AI_REQUIRE_APPROVAL=true: no trade without a valid AI approval, in BOTH modes.\n"
                if result.get("require_approval", True)
                else "AI_REQUIRE_APPROVAL=false"
                + ("" if result.get("rule_fallback_enabled") else " (rule fallback disabled)")
                + "\n"
            )
            + "Switch: /ai_fallback_block or /ai_fallback_technical (owner only, audited).\n"
            "Kill switch, risk checks and news block are never affected."
        )[:3600]
    if section == "limits":
        eff, caps = result["effective"], result["hard_caps"]
        overrides = ", ".join(f"{k}={v}" for k, v in result["ai_overrides"].items()) or "none"
        return (
            f"Active limits (source {eff['source']}): {eff['max_daily_trades']} trades/day · "
            f"{eff['max_open_positions']} positions · risk {eff['risk_percent']}% · "
            f"target {eff['target_usd']}\n"
            f"AI overrides: {overrides}\n"
            "AI bounds: " + ", ".join(f"{k} {lo}-{hi}" for k, (lo, hi) in result["ai_bounds"].items()) + "\n"
            f"Hard caps (AI can never exceed): {caps['max_daily_trades']} trades/day · "
            f"{caps['max_open_positions']} positions · risk {caps['risk_percent_per_trade']}% · "
            f"daily loss {caps['max_daily_loss_percent']}% · drawdown {caps['max_drawdown_percent']}%\n"
            "Changes >50% need your approval (/suggestions). /ai_reset restores defaults."
        )[:3600]
    if section == "ai_stats":
        mode = "required" if result.get("require_approval") else "optional"
        lines = [f"AI stats · decision mode {result.get('decision_mode', '?')} · AI approval {mode}"]
        for row in result.get("labels", [])[:12]:
            win = "-" if row.get("win_rate") is None else f"{row['win_rate']}%"
            conf = "-" if row.get("avg_confidence") is None else f"{row['avg_confidence']}%"
            lines.append(
                f"{row['label']}: approvals {row['approvals']} · rejections {row['rejections']} · "
                f"trades {row['trades']} (closed {row['closed']}) · win {win} · net {row['net_profit']} · "
                f"avg conf {conf} · failures {row['failures']}"
            )
        if len(lines) == 1:
            lines.append("No AI decisions or trades stored yet.")
        configured = ", ".join(
            f"{c['label']}({c['role']},p{c['priority']}{'' if c['enabled'] else ',off'})"
            for c in result.get("configured", [])
        )
        lines.append("Configured: " + (configured or "none"))
        return "\n".join(lines)[:3600]
    if section == "audit":
        from ai.trade_audit import format_report

        if result.get("error"):
            return "Trade audit unavailable (stored data could not be read)."
        return format_report(result)
    if section == "alerts":
        icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨", "CRITICAL": "🛑"}
        lines = [f"Alerts · unacknowledged: {result.get('unacknowledged', 0)}"]
        for row in result.get("items", [])[:10]:
            repeat = f" (x{row['repeat_count']})" if row.get("repeat_count", 1) > 1 else ""
            ack = "✓" if row.get("acknowledged") else "•"
            lines.append(
                f"{ack} #{row['id']} {icons.get(row['level'], '')} {row['level']} {row['timestamp'][:19]}\n"
                f"{row['component']}: {row['message'][:200]}{repeat}"
                + (f"\nAction: {row['action'][:120]}" if row.get("action") else "")
            )
        if len(lines) == 1:
            lines.append("No alerts stored.")
        lines.append("/ack_all to acknowledge all · /ack ID for one.")
        return "\n\n".join(lines)[:3600]
    lines = ["MT5 AI ReflexBot · " + section + " (stored projection)"]
    for row in result.get("items", [])[:10]:
        if section in {"positions", "trades"}:
            lines.append(
                f"#{row['id']} {row['symbol']} {row['direction']} {row['volume']} · {row['status']}\n"
                f"ticket/id: {row.get('ticket')} / {row.get('position_identifier')}\n"
                f"Realized {row.get('profit_account', 'unknown')} {row.get('currency', '')}; "
                "floating P&L unknown\n"
                f"Decided by: {row.get('decided_by', 'unknown')} · trailing: "
                f"{row.get('trailing_by', 'MECHANICAL')} · result: {row.get('result', 'unknown')}"
            )
        elif section == "signals":
            lines.append(
                f"{row['symbol']} {row['direction']} · score {row['score']} · {row['final_decision']}"
            )
        elif section == "news":
            lines.append(f"[{row['impact']}] {row['title']}\nOriginal publication: {row['published_at']}")
        elif section == "suggestions":
            lines.append(
                f"#{row['id']} {row['type']} · {row['status']}\n{row['reason']}\n"
                "Approval is NOT application/trade."
            )
        elif section == "logs":
            lines.append(f"{row['time']} {row['source']} · {row['action']}")
        elif section == "ai_journal":
            where = row.get("symbol") or ""
            if row.get("threshold"):
                where += f" lock {row['threshold']}%"
            confidence = "-" if row.get("confidence") is None else f"{row['confidence']:.0f}"
            outcome = f" · result {row['outcome_usd']} USD" if row.get("outcome_usd") else ""
            lines.append(
                f"#{row['id']} {row['kind']} {where} · {row['action']} ({confidence}) via {row['source']}\n"
                f"→ {row.get('final_action') or row.get('rejection_reason') or 'recorded'}{outcome}\n"
                f"{row['reason'][:160]}"
            )
    if len(lines) == 1:
        lines.append("No stored records. Unknown is not zero or clearance.")
    if section == "ai_journal" and result.get("summary"):
        summary = result["summary"]
        lines.append(
            f"AI answers {summary.get('ai_answers', 0)} · rule fallbacks {summary.get('rule_fallbacks', 0)}"
            " · AI advises; risk engine, pause and kill stay authoritative."
        )
    if section == "news":
        lines.append(
            "Coverage: " + result.get("coverage", {}).get("status", "unknown") + "; not entry permission."
        )
    return "\n\n".join(lines)[:3600]


def action_text(result):
    if result["status"] == "confirmation_required":
        return "Owner confirmation required\n\n" + result["summary"] + "\n\nExpires: " + result["expires_at"]
    message = result.get("message", result.get("reason", "Decision recorded."))
    return f"Owner action: {result['status']}\n{message}\nReplayed outcome: {result.get('replayed', False)}"[
        :3600
    ]
