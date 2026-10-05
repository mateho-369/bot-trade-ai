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
    lines = ["MT5 AI ReflexBot · " + section + " (stored projection)"]
    for row in result.get("items", [])[:10]:
        if section in {"positions", "trades"}:
            lines.append(
                f"#{row['id']} {row['symbol']} {row['direction']} {row['volume']} · {row['status']}\n"
                f"ticket/id: {row.get('ticket')} / {row.get('position_identifier')}\n"
                f"Realized {row.get('profit_account', 'unknown')} {row.get('currency', '')}; "
                "floating P&L unknown"
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
