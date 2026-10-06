"""Failure-isolated local reports with optional Telegram sendMessage-only delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from core.security import sanitize_data, sanitize_text, sha256_json
from core.settings import outbound_report_credentials_valid

LOG = logging.getLogger("reflexbot.reporter")
MAX_TEXT = 1500
MAX_QUEUE = 100
MAX_ACTION_BYTES = 5 * 1024 * 1024
MAX_DAILY_BYTES = 4 * 1024 * 1024
MAX_DETAILS = 1000
EN = {
    "started": (
        "Runtime started PAUSED after reconciliation. Entries remain gated until readiness and "
        "resume conditions pass."
    ),
    "restart": (
        "Watchdog started a replacement. It must reconcile and pass the same resume gates; no "
        "unknown order is resent and no latch is reset."
    ),
    "stalled": (
        "Runtime health is stale. Graceful stop was requested; replacement is withheld while the "
        "child is alive. Inspect MT5 and durable intents."
    ),
    "budget": (
        "Watchdog restart budget is exhausted. Automatic starts have stopped; review logs and "
        "broker outcomes before restarting."
    ),
    "job_failed": (
        "A runtime job failed. Entry safety gates remain authoritative; inspect the local report "
        "and sanitized logs."
    ),
    "stopped": (
        "Runtime stopped PAUSED. Broker-side stop-loss and take-profit remain; local trailing is "
        "unavailable while stopped."
    ),
    "daily_report": (
        "Daily closed-trade report was stored locally. It does not change settings or establish "
        "stage eligibility."
    ),
    "learning_candidate": (
        "Learning produced an inactive model candidate. It cannot affect execution without "
        "separate reviewed evidence."
    ),
    "auto_resumed": (
        "AUTONOMOUS_DEMO resumed entries only after reconciliation, a successful AI health "
        "check, and all shared safety gates passed."
    ),
    "critical_alert_paused": (
        "A CRITICAL alert paused new entries. Automatic recovery waits 15 minutes, requires "
        "every gate to pass, and is limited to three resumes per runtime day."
    ),
    "halt_unknown_execution": (
        "Entries halted: execution outcome is unknown. Reconcile broker history and durable "
        "intents locally; never resend an uncertain order."
    ),
    "halt_broker_unstable": (
        "Entries halted: broker connection is unstable. Verify MT5 connectivity and reconcile "
        "all intents before resuming."
    ),
    "halt_ledger_mismatch": (
        "Entries halted: broker and local ledger disagree. Reconcile the account and correct the "
        "ledger before resuming."
    ),
    "halt_persistence_failure": (
        "Entries halted: durable storage failed. Check disk and database integrity before restarting."
    ),
    "halt_risk_observation_gap": (
        "Entries halted: risk observations are stale or incomplete. Restore fresh, continuous "
        "risk data before resuming."
    ),
    "halt_remedy": (
        "Remedy: inspect the local report, broker state, risk baseline, and unsettled intents. "
        "Use python -m scripts.ops only after the cause is resolved."
    ),
    "protected_positions": (
        "Open positions retain broker-side SL/TP. Do not remove broker protection while the "
        "runtime is paused or stopped."
    ),
    "operator_pause": (
        "Local operator paused new entries. Open positions retain broker-side SL/TP and "
        "protective management continues while the runtime is active."
    ),
    "operator_kill": (
        "Local operator latched the kill switch. New entries remain disabled; open positions "
        "retain broker-side SL/TP."
    ),
    "operator_stop": (
        "Persistent local stop requested. Wait for clean child exit; an in-flight broker write "
        "cannot be recalled."
    ),
    "operator_resumed": (
        "Local operator resumed entry permission after the runtime was reconciled and the shared "
        "safety gates passed. This is not live authorization."
    ),
    "operator_kill_reset": (
        "The local kill latch was reset only after explicit review. The runtime remains paused "
        "and loss/drawdown latches were not cleared."
    ),
    "operator_recovery_reviewed": (
        "A designated halt was reviewed and cleared locally. The runtime remains paused; resume "
        "still requires every shared safety gate."
    ),
    "operator_baseline_reviewed": (
        "A sampled risk baseline was reviewed while reconciled flat. Loss and drawdown latches "
        "were preserved; the runtime remains paused."
    ),
    "operator_stop_cleared": (
        "The persistent local stop marker was explicitly cleared. The runtime remains paused and "
        "no safety latch was reset."
    ),
    "operator_ai_fallback": (
        "Local operator changed the AI failure policy. This does not bypass AI approval, risk, "
        "news, spread, or startup gates."
    ),
    "operator_ai_reset": (
        "Local operator reset AI overlays to reviewed configuration defaults. Risk/capital "
        "latches and broker state were not changed."
    ),
    "operator_proposal_decision": (
        "Local operator recorded an integrity-checked AI proposal decision. This decision alone "
        "does not apply settings, start a model, resume entries, or execute a trade."
    ),
    "news_alert": "News alert",
    "news_symbols": "Symbols",
    "news_coverage_blocked": "Required news/calendar coverage is uncertain; new entries remain blocked.",
    "report_header": "MT5 AI ReflexBot report",
    "label_time": "Time",
    "label_level": "Level",
    "label_component": "Component",
    "label_message": "Message",
    "label_action": "Action",
    "label_repeated": "Repeated",
    "label_rate_limited": "Other reports were rate-limited; see the local reports folder.",
    "label_critical": (
        "New entries paused. Automatic recovery is conditional; local remedy is required for any "
        "failed gate or designated halt."
    ),
    "ai_header": "MT5 AI ReflexBot · AI",
    "ai_trade_opened": "Trade opened",
    "ai_trade_closed": "Trade closed",
    "ai_profit_loss": "Profit/loss",
    "ai_reason": "Reason",
    "ai_entry": "Entry AI",
    "ai_trailing": "Trailing",
    "ai_decision": "Decision",
    "ai_confidence": "Confidence",
    "ai_market": "Market",
    "ai_news_risk": "News risk",
    "ai_suggested_risk": "Suggested risk",
    "ai_reason_label": "Reason",
    "ai_circuit_open": "AI circuit open",
    "ai_circuit_closed": "AI circuit closed; AI answers again.",
    "ai_config": "AI configuration report",
    "ai_provider_open": "provider circuit open",
    "ai_provider_closed": "provider circuit closed (answering again)",
    "ai_blocked": "BLOCKED: no valid AI approval",
    "ai_not_live_authorization": "AI output is advisory and cannot authorize or execute a trade.",
}

KH = {
    "started": (
        "ប្រព័ន្ធបានចាប់ផ្តើមក្នុងស្ថានភាពផ្អាក បន្ទាប់ពីផ្ទៀងផ្ទាត់ការសម្របសម្រួល។ ការបើកប្រតិបត្តិការនៅតែត្រូវឆ្លងកាត់លក្ខខណ្ឌសុវត្ថិភាព។"
    ),
    "restart": (
        "Watchdog បានចាប់ផ្តើមប្រព័ន្ធជំនួស។ ប្រព័ន្ធត្រូវផ្ទៀងផ្ទាត់ "
        "និងឆ្លងកាត់លក្ខខណ្ឌបន្តដូចគ្នា។ មិនផ្ញើបញ្ជាមិនច្បាស់ឡើងវិញ និងមិនលុបឡាចសុវត្ថិភាពទេ។"
    ),
    "stalled": (
        "សុខភាពប្រព័ន្ធយឺតហួសពេល។ បានស្នើឱ្យបញ្ឈប់ដោយសុវត្ថិភាព ហើយមិនជំនួសដំណើរការខណៈវានៅរស់ទេ។ "
        "សូមពិនិត្យ MT5 និងបញ្ជាមិនទាន់ផ្ទៀងផ្ទាត់។"
    ),
    "budget": (
        "អស់ចំនួនចាប់ផ្តើមឡើងវិញសម្រាប់ Watchdog ហើយ។ ការចាប់ផ្តើមស្វ័យប្រវត្តិត្រូវបានបញ្ឈប់។ "
        "សូមពិនិត្យកំណត់ហេតុ និងលទ្ធផលនៅ broker មុនចាប់ផ្តើមឡើងវិញ។"
    ),
    "job_failed": (
        "កិច្ចការមួយរបស់ប្រព័ន្ធបានបរាជ័យ។ "
        "លក្ខខណ្ឌសុវត្ថិភាពសម្រាប់បើកប្រតិបត្តិការនៅតែមានអាទិភាព។ សូមពិនិត្យរបាយការណ៍មូលដ្ឋាន "
        "និងកំណត់ហេតុដែលបានលាក់ព័ត៌មានសម្ងាត់។"
    ),
    "stopped": (
        "ប្រព័ន្ធបានបញ្ឈប់ក្នុងស្ថានភាពផ្អាក។ Stop-loss និង take-profit នៅ broker នៅតែមាន។ "
        "Trailing ក្នុងម៉ាស៊ីនមូលដ្ឋានមិនដំណើរការពេលប្រព័ន្ធឈប់ទេ។"
    ),
    "daily_report": (
        "របាយការណ៍ប្រតិបត្តិការដែលបានបិទប្រចាំថ្ងៃត្រូវបានរក្សាទុកក្នុងមូលដ្ឋាន។ វាមិនផ្លាស់ប្តូរការកំណត់ ឬបញ្ជាក់សិទ្ធិដំណាក់កាលទេ។"
    ),
    "learning_candidate": (
        "ការរៀនបានបង្កើតម៉ូដែលសាកល្បងដែលមិនសកម្ម។ វាមិនអាចប៉ះពាល់ដល់ការប្រតិបត្តិបានទេ បើគ្មានភស្តុតាងពិនិត្យដោយឡែក។"
    ),
    "auto_resumed": (
        "AUTONOMOUS_DEMO បានបន្តការបើកប្រតិបត្តិការតែបន្ទាប់ពីផ្ទៀងផ្ទាត់ការសម្របសម្រួល "
        "ពិនិត្យសុខភាព AI បានជោគជ័យ និងឆ្លងកាត់លក្ខខណ្ឌសុវត្ថិភាពរួមទាំងអស់។"
    ),
    "critical_alert_paused": (
        "ការជូនដំណឹង CRITICAL បានផ្អាកការបើកប្រតិបត្តិការថ្មី។ "
        "ការសង្គ្រោះស្វ័យប្រវត្តិត្រូវរង់ចាំ ១៥ នាទី ឆ្លងកាត់គ្រប់លក្ខខណ្ឌ និងកំណត់អតិបរមា ៣ "
        "ដងក្នុងមួយថ្ងៃនៃ runtime។"
    ),
    "halt_unknown_execution": (
        "បានបញ្ឈប់ការបើកប្រតិបត្តិការ៖ លទ្ធផលបញ្ជាមិនទាន់ច្បាស់។ សូមផ្ទៀងផ្ទាត់ប្រវត្តិ broker "
        "និងបញ្ជាក្នុងមូលដ្ឋាន។ កុំផ្ញើបញ្ជាមិនច្បាស់ឡើងវិញ។"
    ),
    "halt_broker_unstable": (
        "បានបញ្ឈប់ការបើកប្រតិបត្តិការ៖ ការតភ្ជាប់ broker មិនមានស្ថិរភាព។ សូមផ្ទៀងផ្ទាត់ MT5 និងបញ្ជាទាំងអស់មុនបន្ត។"
    ),
    "halt_ledger_mismatch": (
        "បានបញ្ឈប់ការបើកប្រតិបត្តិការ៖ សៀវភៅកត់ត្រា broker និងមូលដ្ឋានមិនត្រូវគ្នា។ សូមផ្ទៀងផ្ទាត់គណនី និងកែសម្រួលសៀវភៅមុនបន្ត។"
    ),
    "halt_persistence_failure": (
        "បានបញ្ឈប់ការបើកប្រតិបត្តិការ៖ ការរក្សាទុកទិន្នន័យមានបញ្ហា។ សូមពិនិត្យថាស និងភាពត្រឹមត្រូវនៃមូលដ្ឋានទិន្នន័យមុនចាប់ផ្តើមឡើងវិញ។"
    ),
    "halt_risk_observation_gap": (
        "បានបញ្ឈប់ការបើកប្រតិបត្តិការ៖ ទិន្នន័យតាមដានហានិភ័យចាស់ ឬមិនពេញលេញ។ សូមស្តារទិន្នន័យថ្មី និងបន្តគ្នាមុនបន្ត។"
    ),
    "halt_remedy": (
        "ដំណោះស្រាយ៖ ពិនិត្យរបាយការណ៍មូលដ្ឋាន ស្ថានភាព broker មូលដ្ឋានហានិភ័យ "
        "និងបញ្ជាមិនទាន់ដោះស្រាយ។ ប្រើ python -m scripts.ops តែបន្ទាប់ពីដោះស្រាយមូលហេតុ។"
    ),
    "protected_positions": ("មុខតំណែងបើកនៅតែមាន SL/TP នៅ broker។ កុំដកការការពារនៅ broker ពេលប្រព័ន្ធផ្អាក ឬឈប់។"),
    "operator_pause": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានផ្អាកការបើកប្រតិបត្តិការថ្មី។ មុខតំណែងបើកនៅតែមាន SL/TP នៅ "
        "broker ហើយការគ្រប់គ្រងការពារបន្តពេល runtime នៅដំណើរការ។"
    ),
    "operator_kill": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានចាក់សោ Kill Switch។ ការបើកប្រតិបត្តិការថ្មីនៅតែបិទ ហើយមុខតំណែងបើកនៅតែមាន SL/TP នៅ broker។"
    ),
    "operator_stop": (
        "បានស្នើឱ្យបញ្ឈប់មូលដ្ឋានជាអចិន្ត្រៃយ៍។ សូមរង់ចាំឱ្យ child ចេញដោយសុវត្ថិភាព។ មិនអាចដកបញ្ជា broker ដែលកំពុងដំណើរការបានទេ។"
    ),
    "operator_resumed": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានអនុញ្ញាតការបើកប្រតិបត្តិការវិញ បន្ទាប់ពីសម្របសម្រួល runtime "
        "និងឆ្លងកាត់លក្ខខណ្ឌសុវត្ថិភាពរួម។ នេះមិនមែនជាការអនុញ្ញាត LIVE ទេ។"
    ),
    "operator_kill_reset": (
        "បានស្តារឡាច kill ក្នុងមូលដ្ឋានតែបន្ទាប់ពីពិនិត្យច្បាស់លាស់។ Runtime នៅតែផ្អាក ហើយឡាចខាត/ការធ្លាក់ចុះមិនត្រូវបានលុបទេ។"
    ),
    "operator_recovery_reviewed": (
        "បានពិនិត្យ និងសម្អាតការបញ្ឈប់ដែលបានកំណត់ក្នុងមូលដ្ឋាន។ Runtime នៅតែផ្អាក; ការបន្តនៅតែត្រូវឆ្លងកាត់គ្រប់លក្ខខណ្ឌសុវត្ថិភាព។"
    ),
    "operator_baseline_reviewed": (
        "បានពិនិត្យមូលដ្ឋានហានិភ័យដែលបានយកគំរូ ខណៈគ្មានមុខតំណែង។ ឡាចខាត/ការធ្លាក់ចុះត្រូវបានរក្សាទុក ហើយ runtime នៅតែផ្អាក។"
    ),
    "operator_stop_cleared": (
        "បានសម្អាតសញ្ញាបញ្ឈប់មូលដ្ឋានដោយចេតនា។ Runtime នៅតែផ្អាក ហើយឡាចសុវត្ថិភាពមិនត្រូវបានកំណត់ឡើងវិញទេ។"
    ),
    "operator_ai_fallback": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានផ្លាស់ប្តូរគោលការណ៍ពេល AI បរាជ័យ។ វាមិនអាចរំលងការអនុម័ត AI "
        "ហានិភ័យ ព័ត៌មាន spread ឬលក្ខខណ្ឌចាប់ផ្តើមទេ។"
    ),
    "operator_ai_reset": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានកំណត់ AI overlays ត្រឡប់ទៅតម្លៃកំណត់ដែលបានពិនិត្យ។ "
        "ឡាចហានិភ័យ/ដើមទុន និងស្ថានភាព broker មិនត្រូវបានផ្លាស់ប្តូរទេ។"
    ),
    "operator_proposal_decision": (
        "ប្រតិបត្តិករក្នុងមូលដ្ឋានបានកត់ត្រាការសម្រេចលើសំណើ AI ដែលបានផ្ទៀងផ្ទាត់សុចរិតភាព។ "
        "ការសម្រេចនេះតែមួយមុខមិនអនុវត្តការកំណត់ មិនដំណើរការម៉ូដែល មិនបន្តការបើកប្រតិបត្តិការ "
        "ឬប្រតិបត្តិការជួញដូរទេ។"
    ),
    "news_alert": "ការជូនដំណឹងព័ត៌មាន",
    "news_symbols": "និមិត្តសញ្ញា",
    "news_coverage_blocked": ("ការគ្របដណ្តប់ព័ត៌មាន/ប្រតិទិនដែលត្រូវការមិនទាន់ប្រាកដ។ ការបើកប្រតិបត្តិការថ្មីនៅតែត្រូវបានទប់ស្កាត់។"),
    "report_header": "របាយការណ៍ MT5 AI ReflexBot",
    "label_time": "ម៉ោង",
    "label_level": "កម្រិត",
    "label_component": "ផ្នែក",
    "label_message": "សារ",
    "label_action": "សកម្មភាព",
    "label_repeated": "កើតឡើងម្តងទៀត",
    "label_rate_limited": "របាយការណ៍ផ្សេងទៀតត្រូវបានកំណត់អត្រា។ សូមមើលថតរបាយការណ៍មូលដ្ឋាន។",
    "label_critical": (
        "ការបើកប្រតិបត្តិការថ្មីត្រូវបានផ្អាក។ ការសង្គ្រោះស្វ័យប្រវត្តិមានលក្ខខណ្ឌ; "
        "ត្រូវការដោះស្រាយក្នុងមូលដ្ឋាន ប្រសិនបើលក្ខខណ្ឌណាមួយបរាជ័យ ឬមានការបញ្ឈប់កំណត់។"
    ),
    "ai_header": "MT5 AI ReflexBot · AI",
    "ai_trade_opened": "បានបើកប្រតិបត្តិការ",
    "ai_trade_closed": "បានបិទប្រតិបត្តិការ",
    "ai_profit_loss": "ចំណេញ/ខាត",
    "ai_reason": "មូលហេតុ",
    "ai_entry": "AI ពេលចូល",
    "ai_trailing": "Trailing",
    "ai_decision": "ការសម្រេចចិត្ត",
    "ai_confidence": "កម្រិតជឿជាក់",
    "ai_market": "ទីផ្សារ",
    "ai_news_risk": "ហានិភ័យព័ត៌មាន",
    "ai_suggested_risk": "ហានិភ័យដែលបានណែនាំ",
    "ai_reason_label": "មូលហេតុ",
    "ai_circuit_open": "សៀគ្វី AI បានបើកការការពារ",
    "ai_circuit_closed": "សៀគ្វី AI បានបិទការការពារ; AI ឆ្លើយតបវិញ។",
    "ai_config": "របាយការណ៍ការកំណត់ AI",
    "ai_provider_open": "សៀគ្វី provider បានបើកការការពារ",
    "ai_provider_closed": "សៀគ្វី provider បានបិទការការពារ (ឆ្លើយតបវិញ)",
    "ai_blocked": "បានទប់ស្កាត់៖ គ្មានការអនុម័ត AI ត្រឹមត្រូវ",
    "ai_not_live_authorization": "លទ្ធផល AI គ្រាន់តែជាព័ត៌មានណែនាំ មិនអាចអនុញ្ញាត ឬប្រតិបត្តិការជួញដូរបានទេ។",
}

CATALOGS = {"en": EN, "km": KH}


def catalog_parity() -> bool:
    return set(EN) == set(KH)


def render_alert(alert, *, language: str, when: datetime, repeats: int = 0, suppressed: int = 0) -> str:
    catalog = CATALOGS.get(language, EN)
    level = {"WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL", "INFO": "INFO"}.get(
        alert.level, "WARNING"
    )
    lines = [
        f"{catalog['report_header']} · {level}",
        f"{catalog['label_time']}: {when.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"{catalog['label_level']}: {level}",
        f"{catalog['label_component']}: {alert.component}",
        f"{catalog['label_message']}: {alert.message}",
    ]
    if alert.action:
        lines.append(f"{catalog['label_action']}: {alert.action}")
    if repeats > 1:
        lines.append(f"{catalog['label_repeated']}: x{repeats}")
    if suppressed:
        lines.append(catalog["label_rate_limited"])
    if alert.level == "CRITICAL":
        lines.append(catalog["label_critical"])
        lines.append(catalog["protected_positions"])
        lines.append(catalog["halt_remedy"])
    return "\n".join(lines)[:MAX_TEXT]


def _valid_config(settings) -> tuple[bool, str, str]:
    token = settings.telegram_bot_token.get_secret_value()
    chat = str(settings.telegram_report_chat_id).strip()
    if not outbound_report_credentials_valid(token, chat):
        return False, "", ""
    return True, token, chat


def reporter_config_valid(settings) -> bool:
    """Validate optional outbound credentials without creating files or opening transport."""
    return _valid_config(settings)[0]


class Reporter:
    """Local-first report sink with an optional HTTPS-only Telegram outbound path."""

    def __init__(self, settings, *, secrets=(), transport=None, console=print, clock=None):
        self.settings, self.console, self.transport, self.clock = settings, console, transport, clock
        self.language = settings.report_language if settings.report_language in CATALOGS else "en"
        self.enabled, self._token, self._chat = _valid_config(settings)
        self._secrets = tuple(secrets) + tuple(value for value in (self._token, self._chat) if value)
        self.outbox: deque[dict[str, Any]] = deque(maxlen=MAX_QUEUE)
        self.dropped = 0
        self._lock = threading.RLock()
        self._seen: set[str] = set()
        self._blocked_at: dict[str, float] = {}
        self._monotonic = __import__("time").monotonic
        self.reports_dir = settings.resolve_path(Path(settings.data_dir) / "reports")
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            LOG.warning("Local report directory unavailable (%s)", type(error).__name__)

    def text(self, key: str) -> str:
        return CATALOGS[self.language][key]

    def _now(self) -> datetime:
        if self.clock is not None:
            return self.clock.now().astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def _safe_details(self, details: dict | None) -> dict:
        try:
            clean = sanitize_data(details or {}, self._secrets)
            packed = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
            if len(packed.encode("utf-8")) > MAX_DETAILS:
                return {"details": "omitted_over_bound"}
            return clean
        except Exception:
            return {"details": "omitted_invalid"}

    def _append(self, path: Path, line: str, maximum: int) -> bool:
        payload = line.encode("utf-8")
        if len(payload) > 8192:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
                return False
            if path.exists() and path.stat().st_size + len(payload) > maximum:
                return False
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            return True
        except OSError as error:
            LOG.warning("Local report write unavailable (%s)", type(error).__name__)
            return False

    def record(self, kind: str, text: str, *, details: dict | None = None) -> dict[str, Any] | None:
        now = self._now()
        safe_kind = sanitize_text(str(kind), self._secrets)[:48]
        safe_text = sanitize_text(str(text), self._secrets).replace("\r", " ").replace("\n", " ")[:MAX_TEXT]
        safe_details = self._safe_details(details)
        record = {
            "id": uuid4().hex,
            "time": now.isoformat(),
            "kind": safe_kind,
            "language": self.language,
            "message": safe_text,
            "details": safe_details,
        }
        human = f"{record['time']} [{safe_kind}] {safe_text}\n"
        day_path = self.reports_dir / (now.date().isoformat() + ".jsonl")
        row = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            written = self._append(self.reports_dir / "actions.log", human, MAX_ACTION_BYTES)
            daily_written = self._append(day_path, row, MAX_DAILY_BYTES)
        if not (written and daily_written):
            LOG.warning("A report was mirrored but local file persistence was incomplete")
        try:
            self.console(f"[REPORT] {safe_text}")
        except Exception as error:
            LOG.warning("Console report mirror unavailable (%s)", type(error).__name__)
        return record

    def queue_text(self, kind: str, text: str, *, details: dict | None = None, dedup: str | None = None):
        identity = sha256_json({"kind": kind, "dedup": dedup}) if dedup is not None else None
        with self._lock:
            if identity is not None and identity in self._seen:
                return None
            if identity is not None:
                self._seen.add(identity)
            record = self.record(kind, text, details=details)
            if record is None:
                return None
            if len(self.outbox) == self.outbox.maxlen:
                self.dropped += 1
            self.outbox.append(record)
        return record

    def queue_event(self, key: str, *, dedup: str, details: dict | None = None):
        if key not in CATALOGS["en"] or not isinstance(dedup, str) or not 1 <= len(dedup) <= 256:
            raise ValueError("known report key and bounded deduplication identity required")
        return self.queue_text("runtime." + key, self.text(key), details=details, dedup=dedup)

    async def deliver(self, record: dict[str, Any]) -> str:
        if not self.enabled:
            return "disabled"
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(connect=2.0, read=4.0, write=4.0, pool=2.0),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json={
                        "chat_id": self._chat if self._chat.startswith("@") else int(self._chat),
                        "text": record["message"][:MAX_TEXT],
                        "parse_mode": None,
                        "disable_web_page_preview": True,
                    },
                )
            if response.status_code != 200:
                return "uncertain"
            payload = response.json()
            return "sent" if isinstance(payload, dict) and payload.get("ok") is True else "uncertain"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOG.warning(
                "Telegram report delivery unavailable (%s); local report retained", type(error).__name__
            )
            return "uncertain"

    async def publish(self, text: str, *, kind: str = "report", details: dict | None = None) -> str:
        record = self.record(kind, text, details=details)
        return "unavailable" if record is None else await self.deliver(record)

    async def drain(self, *, limit: int = 10) -> dict:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded report batch required")
        result = {
            "sent": 0,
            "uncertain": 0,
            "disabled": 0,
            "queued": len(self.outbox),
            "dropped": self.dropped,
        }
        for _ in range(min(limit, len(self.outbox))):
            with self._lock:
                if not self.outbox:
                    break
                record = self.outbox.popleft()  # Claim before network I/O: no blind retry.
            status = await self.deliver(record)
            result[status if status in {"sent", "uncertain", "disabled"} else "uncertain"] += 1
        result["queued"] = len(self.outbox)
        return result

    async def close(self) -> None:
        return None

    # AI-first notification hooks. They produce bounded reports, never control requests.
    def decision(self, result, symbol: str) -> None:
        if result.kind == "entry" and result.executable and result.decision.opens:
            self.queue_text(
                "ai.decision",
                f"{self.text('ai_header')} · {self.text('ai_decision')} · {symbol}: "
                f"{result.decision.action}; {self.text('ai_confidence')} "
                f"{result.decision.confidence:.0f}. {self.text('ai_not_live_authorization')}",
            )

    def blocked(self, symbol: str, reason: str) -> None:
        now = self._monotonic()
        if now - self._blocked_at.get(symbol, -1e9) < 900:
            return
        self._blocked_at[symbol] = now
        self.queue_text("ai.blocked", f"{self.text('ai_blocked')} · {symbol} ({str(reason)[:60]})")

    def trade_opened(self, item: dict) -> None:
        self.queue_text(
            "ai.trade_opened",
            f"{self.text('ai_header')} · {self.text('ai_trade_opened')} · "
            f"{item.get('symbol', '?')} {str(item.get('side', '?')).upper()} "
            f"{item.get('volume', '?')}. {self.text('ai_not_live_authorization')}",
        )

    def trade_closed(self, item: dict) -> None:
        self.queue_text(
            "ai.trade_closed",
            f"{self.text('ai_header')} · {self.text('ai_trade_closed')} · {item.get('symbol', '?')} "
            f"{str(item.get('side', '?')).upper()} · {self.text('ai_profit_loss')}: "
            f"{item.get('profit', '?')} {item.get('currency') or ''} · "
            f"{self.text('ai_reason')}: {item.get('close_reason') or 'unknown'}",
        )

    def provider_event(self, event: str, label: str, detail: str) -> None:
        if event in {"circuit_open", "circuit_closed"}:
            text = self.text("ai_provider_open" if event == "circuit_open" else "ai_provider_closed")
            self.queue_text("ai.provider", f"{self.text('ai_header')} · {label} {text} ({detail[:48]})")

    def circuit_opened(self, *, reason: str, failures: int, deep: bool = False) -> None:
        text = self.text("ai_circuit_open")
        self.queue_text(
            "ai.circuit", f"{self.text('ai_header')} · {text}: {failures} failures ({reason[:48]})."
        )

    def circuit_closed(self, *, deep: bool = False) -> None:
        self.queue_text("ai.circuit", f"{self.text('ai_header')} · {self.text('ai_circuit_closed')}")

    def summary(self, text: str) -> None:
        self.queue_text("ai.summary", f"{self.text('ai_header')} · {str(text)[:900]}")

    def config_adjustment(self, result) -> None:
        if result.status in {"applied", "pending"}:
            self.queue_text(
                "ai.config",
                f"{self.text('ai_config')} · {result.status}: {result.parameter} → {result.value}; "
                f"{str(result.reason)[:220]}. {self.text('ai_not_live_authorization')}",
            )

    def trailing(self, event) -> None:
        if event.source == "ai" or event.final_action == "mechanical_continue":
            self.queue_text(
                "ai.trailing",
                f"{self.text('ai_trailing')} · position {event.position_id}: {event.final_action}. "
                f"{str(event.ai_reason)[:200]}",
            )


def reporter_catalogs() -> dict[str, dict[str, str]]:
    return {language: dict(values) for language, values in CATALOGS.items()}
