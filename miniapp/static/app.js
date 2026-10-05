"use strict";
// No innerHTML, initDataUnsafe, cookie/storage auth, URL credentials or local owner fallback.
(() => {
  const preview = document.documentElement.dataset.preview === "true";
  const names = {dashboard: "Overview", positions: "Positions", trades: "Trade history", signals: "Signals", news: "News & calendar", suggestions: "AI supervisor", ai_journal: "AI journal", settings: "Settings", alerts: "Alerts", logs: "Audit trail"};
  const endpoints = {dashboard: "dashboard", positions: "positions", trades: "trades", signals: "signals", news: "news", suggestions: "ai_suggestions", ai_journal: "ai_journal", settings: "settings", alerts: "alerts", logs: "logs"};
  const actions = new Set(["pause", "resume", "kill", "close_position", "close_all", "approve_suggestion", "reject_suggestion", "ai_fallback", "ai_reset", "alerts/ack"]);
  const state = {data: {}, section: "dashboard", verified: false, busy: false, pending: null, uncertain: false, curveRange: "day", alertLevel: ""};
  let signedInitData = ""; // Memory ONLY; never printed, persisted, echoed or refreshed by client time.
  let refreshTimer = null;
  let alertTimer = null; // Alert Center: 30 s refresh, independent of the 20 s section refresh.
  let expiryTimer = null;
  const $ = (id) => document.getElementById(id);
  const put = (id, value) => { $(id).textContent = value; };
  const element = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = String(text); return node; };
  const svgElement = (tag, attrs = {}) => { const node = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v)); return node; };
  const icon = (name) => { const node = svgElement("svg", {class: "icon", "aria-hidden": "true"}); node.append(svgElement("use", {href: "#i-" + name})); return node; };
  const finite = (value) => value === null || value === undefined || value === "" ? null : Number.isFinite(Number(value)) ? Number(value) : null;
  const money = (value, currency = "USD", signed = false) => { const number = finite(value); if (number === null) return "—"; try { return new Intl.NumberFormat("en-US", {style: "currency", currency, signDisplay: signed ? "exceptZero" : "auto", minimumFractionDigits: 2, maximumFractionDigits: 2}).format(number); } catch { return (signed && number > 0 ? "+" : "") + number.toFixed(2) + " " + String(currency); } };
  const time = (value, full = false) => { if (!value || !Number.isFinite(Date.parse(value))) return "Unknown time"; return new Intl.DateTimeFormat("en-GB", {timeZone: "UTC", ...(full ? {day: "2-digit", month: "short"} : {}), hour: "2-digit", minute: "2-digit"}).format(new Date(value)); };
  const human = (value) => String(value || "unknown").replaceAll("_", " ");
  const empty = (holder, text) => holder.replaceChildren(element("div", "empty-state", text));

  function notice(message, bad = false) { const node = $("notice"); node.textContent = message; node.classList.toggle("error", bad); node.hidden = false; }
  function locked(message, expired = false) {
    state.verified = false; signedInitData = ""; put("owner-access-caption", "Backend-verified identity only"); state.data = {}; state.pending = null; state.busy = false;
    clearInterval(refreshTimer); clearInterval(expiryTimer); clearInterval(alertTimer);
    $("authenticated-view").hidden = true; $("locked-view").hidden = false;
    put("locked-title", expired ? "Your owner session expired." : "Your console stays private.");
    put("locked-description", message); put("session-label", expired ? "Reopen in Telegram" : "Not authenticated");
    $("mode-badge").replaceChildren(element("span"), document.createTextNode("LOCKED"));
    if ($("confirmation-dialog").open) $("confirmation-dialog").close();
    // Wipe previous sensitive projections, not just hide them until a future login.
    for (const id of ["overview-positions", "positions-table", "trades-table", "signals-list", "news-list", "calendar-list", "overview-calendar", "suggestions-list", "ai-journal-list", "ai-adjustments-list", "settings-list", "logs-list", "alerts-list", "equity-chart"]) $(id).replaceChildren();
    for (const id of ["ai-fallback-mode", "nav-alert-count", "alert-unack"]) put(id, "—");
    for (const id of ["balance-value", "equity-value", "today-value", "positions-value", "curve-value"]) put(id, "—");
    updateButtons();
  }

  async function api(path, body) {
    if (preview || !state.verified || !signedInitData) throw new Error("owner_authentication_required");
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch("/api/" + path, {
        method: body ? "POST" : "GET", credentials: "omit", cache: "no-store", redirect: "error",
        headers: {"X-Telegram-Init-Data": signedInitData, ...(body ? {"Content-Type": "application/json"} : {})},
        ...(body ? {body: JSON.stringify(body)} : {}), signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) {
        const code = result.error?.code || "request_denied";
        if (response.status === 401 || response.status === 403) {
          locked("Access is locked. Close and reopen the Mini App from your configured owner's private Telegram bot. No local login or client expiry refresh is available.", response.status === 401);
        }
        throw new Error(code);
      }
      return result;
    } catch (error) {
      // Network/aborted POST could have committed. No automatic mutation retry.
      if (body && (error.name === "AbortError" || error instanceof TypeError)) throw new Error("action_outcome_unknown_do_not_retry");
      if (error instanceof TypeError || error.name === "AbortError") throw new Error("stored_data_unavailable");
      throw error;
    } finally { clearTimeout(deadline); }
  }

  function updateButtons() {
    const caps = state.data.dashboard?.capabilities || {};
    const enabled = state.verified && !preview && !state.busy;
    const definitions = {"pause-button": enabled && caps.pause, "kill-button": enabled && caps.kill,
      "resume-button": enabled && caps.resume && !state.uncertain, "close-all-button": enabled && caps.close_owned && !state.uncertain};
    const fallback = state.data.settings?.ai_fallback?.mode, unacknowledged = state.data.alerts?.unacknowledged || 0;
    Object.assign(definitions, {"fallback-block-button": enabled && fallback !== "BLOCK_ON_AI_FAILURE", "fallback-technical-button": enabled && fallback !== "TECHNICAL_ONLY", "ai-reset-button": enabled, "ack-all-button": enabled && unacknowledged > 0});
    for (const [id, value] of Object.entries(definitions)) { $(id).disabled = !value; $(id).title = preview ? "Disabled: synthetic read-only UI fixture" : value ? "Fresh owner request; existing gates still apply" : "Owner/runtime capability unavailable"; }
    for (const node of document.querySelectorAll("[data-owner-action]")) node.disabled = !enabled || state.uncertain || node.dataset.eligible !== "true";
  }

  function renderDashboard() {
    const data = state.data.dashboard; if (!data) return;
    const settings = state.data.settings?.values || {};
    const kpis = data.kpis; const currency = kpis.currency || "USD";
    $("mode-badge").replaceChildren(element("span"), document.createTextNode(String(data.meta.mode).toUpperCase() + (preview ? " · FIXTURE" : " MODE")));
    put("balance-value", money(kpis.balance, currency)); put("equity-value", money(kpis.equity, currency));
    put("today-value", money(kpis.realized_today_account, currency, true));
    $("today-value").classList.toggle("danger-text", finite(kpis.realized_today_account) !== null && Number(kpis.realized_today_account) < 0);
    put("balance-foot", preview ? "Illustrative account currency: " + currency : "Account currency: " + currency);
    put("equity-foot", preview ? "Artificial figure · not performance" : kpis.observation_current ? "Last stored observation · not live" : "Stale / missing observation");
    put("today-foot", preview ? "Invented P&L · no evidence" : kpis.today_currency_verified ? "Stored realized ledger · " + currency : "Unverified / unknown current ledger");
    const value = element("span", "", kpis.open_count ?? "—"); value.append(element("span", "kpi-denominator", " / " + (settings.max_open_positions ?? "—"))); $("positions-value").replaceChildren(value);
    put("nav-position-count", kpis.open_count ?? "—"); put("position-count", kpis.open_count ?? "—");
    $("runtime-state").replaceChildren(element("span"), document.createTextNode(human(data.runtime.desired_state) + (preview ? " · fixture" : " entries")));
    put("daily-loss-limit", (settings.max_daily_loss_percent ?? "—") + "%"); put("drawdown-limit", (settings.max_drawdown_percent ?? "—") + "%");
    put("trade-risk-limit", (settings.max_risk_percent_per_trade ?? "—") + "%");
    put("daily-entry-count", (kpis.accepted_entries_today ?? "—") + " / " + (data.targets?.daily_maximum ?? "—") + " max");
    $("guard-status").replaceChildren(icon("lock"), document.createTextNode(preview ? "Synthetic data cannot grant new-entry permission." : data.runtime.recovery_required ? "Recovery / unresolved state. New entries remain gated." : "New-entry permission is not granted by this page."));
    put("curve-value", money(kpis.equity, currency)); put("curve-source", preview ? "SYNTHETIC OBSERVATIONS" : kpis.observation_at ? "LAST STORED · " + time(kpis.observation_at) + " UTC" : "NO OBSERVATIONS");
    put("curve-subtitle", "Stored account observations · " + currency + (preview ? " · artificial" : " · not performance proof"));
    renderCurve(data.equity_curve || []);
    renderPositions($("overview-positions"), data.positions || state.data.positions?.items || [], true);
    renderCalendar($("overview-calendar"), (state.data.news?.calendar || []).slice(0, 2));
    const pending = (state.data.suggestions?.items || []).filter((row) => row.status === "pending").length;
    put("proposal-count", pending + " pending proposal" + (pending === 1 ? "" : "s") + (preview ? " · fixture" : ""));
    put("source-footer", preview ? "Synthetic UI only · no stage evidence" : "SQL projections · not trading permission");
    updateButtons();
  }

  function renderCurve(rows) {
    const chart = $("equity-chart"); chart.replaceChildren();
    let values = rows.filter((row) => finite(row.equity) !== null && Number.isFinite(Date.parse(row.time)));
    if (state.curveRange === "day" && values.length) { const latest = Date.parse(values[values.length - 1].time); values = values.filter((row) => latest - Date.parse(row.time) <= 86400000); }
    const width = 800, height = 230, left = 12, right = 62, top = 12, bottom = 36;
    if (values.length < 2) { chart.append(svgElement("text", {x: 380, y: 110, "text-anchor": "middle", class: "chart-label"})); chart.firstChild.textContent = "At least two stored observations are needed. No fabricated curve."; return; }
    const low = Math.min(...values.map((row) => Number(row.equity))); const high = Math.max(...values.map((row) => Number(row.equity))); const spread = Math.max(high - low, Math.abs(high) * .003, 1); const min = low - spread * .2, max = high + spread * .2;
    const x = (index) => left + index / (values.length - 1) * (width - left - right);
    const y = (value) => top + (max - value) / (max - min) * (height - top - bottom);
    const defs = svgElement("defs"); const gradient = svgElement("linearGradient", {id: "equity-fill", x1: "0", x2: "0", y1: "0", y2: "1"}); gradient.append(svgElement("stop", {offset: "0%", "stop-color": "#61d9b6", "stop-opacity": ".16"}), svgElement("stop", {offset: "100%", "stop-color": "#61d9b6", "stop-opacity": "0"})); defs.append(gradient); chart.append(defs);
    for (let i = 0; i < 4; i++) { const point = top + i / 3 * (height - top - bottom); chart.append(svgElement("line", {x1: left, x2: width - right, y1: point, y2: point, class: "chart-grid"})); const label = svgElement("text", {x: width - right + 14, y: point + 3, class: "chart-label"}); label.textContent = (max - i / 3 * (max - min)).toFixed(2); chart.append(label); }
    const points = values.map((row, index) => [x(index), y(Number(row.equity))]);
    const path = points.map(([px, py], index) => (index ? "L" : "M") + px.toFixed(2) + " " + py.toFixed(2)).join(" ");
    chart.append(svgElement("path", {d: path + " L" + x(values.length - 1) + " " + (height - bottom) + " L" + left + " " + (height - bottom) + " Z", fill: "url(#equity-fill)"}));
    chart.append(svgElement("path", {d: path, class: "chart-line"})); const finalPoint = points[points.length - 1]; chart.append(svgElement("circle", {cx: finalPoint[0], cy: finalPoint[1], r: 4, class: "chart-dot"}));
    for (const index of [0, Math.floor((values.length - 1) / 2), values.length - 1]) { const label = svgElement("text", {x: x(index), y: height - 6, class: "chart-label", "text-anchor": index === 0 ? "start" : index === values.length - 1 ? "end" : "middle"}); label.textContent = time(values[index].time) + " UTC"; chart.append(label); }
    chart.onpointermove = (event) => { const box = chart.getBoundingClientRect(); const index = Math.max(0, Math.min(values.length - 1, Math.round((event.clientX - box.left) / box.width * (values.length - 1)))); put("chart-tooltip", time(values[index].time) + " UTC · " + money(values[index].equity, state.data.dashboard?.kpis.currency)); $("chart-tooltip").hidden = false; };
    chart.onpointerleave = () => { $("chart-tooltip").hidden = true; };
  }

  function side(direction) { return element("span", "side-badge " + (direction === "sell" ? "sell" : direction === "wait" ? "wait" : ""), String(direction || "unknown").toUpperCase()); }
  function symbol(row) { const cell = element("div", "symbol-cell"); const gold = row.symbol?.startsWith("XAU"); const crypto = row.symbol?.startsWith("BTC") || row.symbol?.startsWith("ETH"); const avatar = element("span", "asset-avatar" + (gold ? " gold" : crypto ? " crypto" : ""), gold ? "Au" : crypto ? "₿" : row.symbol?.startsWith("EUR") ? "€" : "£"); const text = element("div"); text.append(element("span", "symbol-name", row.symbol || "Unknown"), element("span", "symbol-caption", row.strategy || "Stored position")); cell.append(avatar, text); return cell; }
  function tableHead(table, labels) { const head = element("thead"), line = element("tr"); for (const label of labels) line.append(element("th", "", label)); head.append(line); table.append(head); }
  function cell(row, value, cls = "") { const node = element("td", cls); if (value instanceof Node) node.append(value); else node.textContent = String(value ?? "—"); row.append(node); }
  function renderPositions(holder, rows, overview = false) {
    if (!rows.length) { empty(holder, "No stored open positions. Stay selective; never force a trade."); return; }
    const table = element("table"); tableHead(table, ["Symbol / strategy", "Side", "Volume", "Stop loss", "Take profit", "Profit lock", "Floating P&L", ...(overview ? [] : ["Owner action"])]);
    const body = element("tbody");
    for (const data of rows) { const row = element("tr"); cell(row, symbol(data)); cell(row, side(data.direction)); cell(row, data.volume); cell(row, data.sl); cell(row, data.tp); const lock = element("span", "lock-badge"); lock.append(icon("lock"), document.createTextNode((data.lock_level ?? "—") + "%")); cell(row, lock); cell(row, "Not observed", "pnl-unknown"); if (!overview) { const button = element("button", "close-row", "Close…"); button.dataset.ownerAction = "close_position"; button.dataset.eligible = String(Boolean(data.owned_close_candidate)); button.title = "Requires a fresh exact bot-owned capture and confirmation"; button.disabled = true; button.onclick = () => runAction("close_position", {ticket: data.ticket, position_identifier: data.position_identifier}); cell(row, button); } body.append(row); }
    table.append(body); holder.replaceChildren(table); updateButtons();
  }
  function renderTrades() { const holder = $("trades-table"), rows = state.data.trades?.items || []; if (!rows.length) return empty(holder, "No stored trades in this mode/account cohort."); const table = element("table"); tableHead(table, ["Symbol / strategy", "Side", "Volume", "Status", "Realized (account)", "Closed (UTC)"]); const body = element("tbody"); for (const data of rows) { const row = element("tr"); cell(row, symbol(data)); cell(row, side(data.direction)); cell(row, data.volume); cell(row, human(data.status)); cell(row, money(data.profit_account, data.currency, true), finite(data.profit_account) !== null && Number(data.profit_account) < 0 ? "danger-text" : "mint"); cell(row, time(data.closed_at, true)); body.append(row); } table.append(body); holder.replaceChildren(table); }
  function renderSignals() { const holder = $("signals-list"), rows = state.data.signals?.items || []; if (!rows.length) return empty(holder, "No stored decisions. No signal is better than a forced entry."); holder.replaceChildren(); for (const data of rows) { const row = element("article", "record-row"), main = element("div", "record-main"); main.append(element("h3", "", data.symbol + " · " + data.strategy), element("p", "", data.timeframe + " · " + human(data.final_decision) + " · AI score " + (data.ai_score ?? "unknown"))); const meta = element("div", "record-meta"); meta.append(side(data.direction), element("span", "", time(data.time, true) + " UTC"), element("span", "", data.config_current ? "Current config cohort" : "Historical / fixture cohort")); main.append(meta); const score = element("div", "record-score", data.score ?? "—"); score.append(element("small", "", "TECHNICAL SCORE")); row.append(main, score); holder.append(row); } }
  function renderCalendar(holder, rows) { if (!rows.length) return empty(holder, "Calendar coverage unknown. No eligible event clearance is implied."); holder.replaceChildren(); for (const data of rows) { const row = element("div", "calendar-item"), clock = element("div", "event-time", time(data.starts_at)); clock.append(element("small", "", "UTC")); const copy = element("div", "event-copy"); copy.append(element("strong", "", data.title), element("span", "", data.currency + " · " + human(data.impact) + (data.tentative ? " · tentative" : ""))); row.append(clock, copy, element("span", "impact-dot " + (data.impact === "high" ? "high" : ""))); holder.append(row); } }
  function renderNews() { const data = state.data.news || {}, holder = $("news-list"), rows = data.items || []; put("news-coverage", "Coverage: " + human(data.coverage?.status) + ". " + (data.coverage?.reason || "Managed projections are scoped and expiring, NOT permission from this page.")); if (!rows.length) empty(holder, "No stored headlines. Missing coverage is not safe coverage."); else { holder.replaceChildren(); for (const data of rows) { const row = element("article", "record-row"), main = element("div", "record-main"); main.append(element("h3", "", data.title), element("p", "", data.summary)); const meta = element("div", "record-meta"); meta.append(element("span", "unknown-badge", human(data.impact)), element("span", "", data.source), element("span", "", "Published " + time(data.published_at, true) + " UTC")); main.append(meta); row.append(main); holder.append(row); } } renderCalendar($("calendar-list"), data.calendar || []); }
  function renderSuggestions() { const holder = $("suggestions-list"), rows = state.data.suggestions?.items || []; if (!rows.length) return empty(holder, "No stored proposals. No autonomous parameter escalation."); holder.replaceChildren(); for (const data of rows) { const card = element("article", "panel suggestion-card"), heading = element("div", "panel-heading"); heading.append(element("span", "eyebrow", "PROPOSAL #" + data.id), element("span", "proposal-status", human(data.status))); card.append(heading, element("h3", "", human(data.type)), element("p", "", data.reason)); const parameters = Object.entries(data.parameters || {}).map(([key, value]) => human(key) + ": " + (typeof value === "object" ? Object.entries(value).map(([k, v]) => human(k) + " " + v).join(" · ") : value)).join(" · "); if (parameters) card.append(element("div", "proposal-parameters", parameters)); card.append(element("p", "", "Approval only. No settings application, risk change or trade execution.")); const controls = element("div", "proposal-actions"); for (const [action, text, cls] of [["approve_suggestion", "Approve only…", "button-primary"], ["reject_suggestion", "Reject", "button-muted"]]) { const button = element("button", "button " + cls, text); button.dataset.ownerAction = action; button.dataset.eligible = String(data.status === "pending" && data.config_current && Boolean(state.data.dashboard?.capabilities.decide_proposal)); button.disabled = true; button.onclick = () => runAction(action, {suggestion_id: data.id}); controls.append(button); } card.append(controls); holder.append(card); } updateButtons(); }
  function renderSettings() { const holder = $("settings-list"), values = state.data.settings?.values || {}; holder.replaceChildren(); const labels = {max_risk_percent_per_trade: "Per-trade risk (%)", max_daily_loss_percent: "Daily loss limit (%)", max_drawdown_percent: "Drawdown limit (%)", target_net_profit_usd: "Net target (USD, not guaranteed)", min_daily_trades_target: "Activity target (never forced)", live_trading: "Live configured (not live approval)"}; for (const [key, value] of Object.entries(values)) { const row = element("div", "setting-row"); let rendered = Array.isArray(value) ? value.map((item) => Array.isArray(item) ? item.join(" → ") : item).join(" · ") : value && typeof value === "object" ? Object.entries(value).map(([k, v]) => human(k) + " " + v).join(" · ") : String(value); row.append(element("span", "", labels[key] || human(key)), element("span", "", rendered)); holder.append(row); } updateButtons(); }
  function renderLogs() { const holder = $("logs-list"), rows = state.data.logs?.items || []; if (!rows.length) return empty(holder, "No stored audit metadata."); holder.replaceChildren(); for (const data of rows) { const row = element("div", "record-row"), main = element("div", "record-main"); main.append(element("h3", "", data.action), element("p", "", data.source + " · record #" + data.id)); row.append(main, element("span", "small-label", time(data.time, true) + " UTC")); holder.append(row); } }
  function renderAIJournal() { const data = state.data.ai_journal || {}, holder = $("ai-journal-list"), rows = data.items || [], summary = data.summary || {}; put("ai-journal-summary", rows.length ? "AI " + (summary.ai_answers || 0) + " · RULE " + (summary.rule_fallbacks || 0) : "NO DECISIONS YET"); if (!rows.length) empty(holder, "No AI decisions recorded yet. Unknown is not approval."); else { holder.replaceChildren(); for (const item of rows) { const row = element("div", "record-row"), main = element("div", "record-main"); const where = (item.symbol || "") + (item.threshold ? " · lock " + item.threshold + "%" : ""); const confidence = item.confidence === null || item.confidence === undefined ? "–" : Math.round(item.confidence); main.append(element("h3", "", human(item.kind) + " · " + human(item.action) + " (" + confidence + ")" + (where ? " · " + where : "")), element("p", "", item.reason), element("p", "", "Source " + human(item.source) + " → " + human(item.final_action || item.rejection_reason || "recorded") + (item.outcome_usd ? " · result " + item.outcome_usd + " USD" : ""))); row.append(main, element("span", "small-label", time(item.time, true) + " UTC")); holder.append(row); } } const adjust = $("ai-adjustments-list"), changes = data.adjustments || []; if (!changes.length) return empty(adjust, "No AI configuration adjustments."); adjust.replaceChildren(); for (const change of changes) { const row = element("div", "record-row"), main = element("div", "record-main"); main.append(element("h3", "", human(change.parameter) + " → " + change.value), element("p", "", human(change.classification) + " · " + human(change.status) + (change.suggestion_id ? " · proposal #" + change.suggestion_id : ""))); row.append(main, element("span", "small-label", time(change.time, true) + " UTC")); adjust.append(row); } }
  function renderFallback() { const data = state.data.settings?.ai_fallback; if (!data) return; const limits = data.limits || {}, ai = data.ai || {}; put("ai-fallback-mode", data.mode === "TECHNICAL_ONLY" ? "TECHNICAL ONLY" : "BLOCK ON AI FAILURE"); put("ai-fallback-description", (data.description || "") + " · AI " + (ai.available ? "available" : "unavailable") + ". Risk checks, news block and kill switch always apply."); put("ai-limits-line", "Active limits (" + human(limits.source) + "): " + (limits.max_daily_trades ?? "—") + " trades/day · " + (limits.max_open_positions ?? "—") + " positions · risk " + (limits.risk_percent ?? "—") + "% · target " + (limits.target_usd ?? "—") + ". Hard caps 25 / 5 / 1.0% never change."); updateButtons(); }
  function renderAlerts() {
    const data = state.data.alerts || {}, holder = $("alerts-list"), rows = data.items || [], unacknowledged = data.unacknowledged || 0;
    put("nav-alert-count", unacknowledged); put("alert-unack", unacknowledged + " unacknowledged");
    for (const node of document.querySelectorAll("[data-alert-level]")) node.classList.toggle("selected", node.dataset.alertLevel === state.alertLevel);
    if (!rows.length) { empty(holder, state.alertLevel ? "No " + state.alertLevel + " alerts stored." : "No alerts stored."); updateButtons(); return; }
    holder.replaceChildren();
    for (const data of rows) {
      const row = element("div", "record-row alert-row level-" + data.level + (data.acknowledged ? " acked" : "")), main = element("div", "record-main"), meta = element("div", "record-meta");
      main.append(element("h3", "", data.component + ": " + data.message), element("p", "", data.action ? "Action: " + data.action : "No action required"));
      meta.append(element("span", "alert-level level-" + data.level, data.level), element("span", "", time(data.timestamp, true) + " UTC"));
      if (data.repeat_count > 1) meta.append(element("span", "", "Repeated x" + data.repeat_count));
      meta.append(element("span", "", "Telegram: " + human(data.telegram_status)));
      main.append(meta);
      const side = element("div", "inline-actions");
      if (data.acknowledged) side.append(element("span", "small-label", "Acknowledged"));
      else { const button = element("button", "button button-muted", "Acknowledge"); button.dataset.ownerAction = "alerts/ack"; button.dataset.eligible = "true"; button.onclick = () => runAction("alerts/ack", {alert_id: data.id}); side.append(button); }
      row.append(main, side); holder.append(row);
    }
    updateButtons();
  }
  function renderSection(section) { ({dashboard: renderDashboard, positions: () => renderPositions($("positions-table"), state.data.positions?.items || []), trades: renderTrades, signals: renderSignals, news: renderNews, suggestions: renderSuggestions, ai_journal: renderAIJournal, settings: () => { renderSettings(); renderFallback(); }, alerts: renderAlerts, logs: renderLogs})[section](); }

  async function load(section) { if (!preview) state.data[section] = await api(section === "alerts" && state.alertLevel ? "alerts?level=" + state.alertLevel : endpoints[section]); renderSection(section); if (section !== "dashboard" && state.data.dashboard) renderDashboard(); }
  async function navigate(section) { if (!Object.hasOwn(names, section)) return; state.section = section; for (const node of document.querySelectorAll("[data-section]")) node.classList.toggle("active", node.dataset.section === section); for (const key of Object.keys(names)) $("view-" + key).hidden = key !== section; put("breadcrumb-title", names[section]); const titles = {dashboard: "Trading overview", positions: "Position control", trades: "Trade history", signals: "Signal decisions", news: "News & calendar", suggestions: "AI supervisor", ai_journal: "AI decision journal", settings: "Your safety settings", alerts: "Alert Center", logs: "Every decision, recorded"}; $("page-title").replaceChildren(document.createTextNode(titles[section]), element("span", "", ".")); put("page-subtitle", section === "dashboard" ? "Quality over activity. Protection before profit." : "Owner-only. Bounded observations. No permission implied by a score or page."); $("sidebar").classList.remove("open"); $("menu-toggle").setAttribute("aria-expanded", "false"); if (state.verified || preview) { try { await load(section); } catch (error) { notice("Stored-data request denied: " + human(error.message) + ".", true); } } }

  function outcome(result) { const bad = result.status === "uncertain" || result.status === "rejected"; if (result.status === "uncertain") state.uncertain = true; notice("Owner action " + result.status + ": " + (result.message || human(result.reason) || "Decision recorded only.") + (result.replayed ? " Cached outcome — not a new execution." : ""), bad); updateButtons(); }
  function openConfirmation(result, action, parameters, requestId) {
    state.pending = {action, parameters, requestId, token: result.confirmation_token, expires: Date.parse(result.expires_at)};
    put("confirmation-summary", result.summary); $("confirmation-check").checked = false; $("confirm-action").disabled = true;
    clearInterval(expiryTimer);
    const tick = () => { if (!state.pending) return; const seconds = Math.max(0, Math.ceil((state.pending.expires - Date.now()) / 1000)); put("confirmation-expiry", seconds ? "Expires in " + seconds + "s · server enforces expiry" : "Expired — cancel and prepare a fresh request"); $("confirm-action").disabled = !seconds || !$("confirmation-check").checked || state.busy; };
    tick(); expiryTimer = setInterval(tick, 500); $("confirmation-dialog").showModal();
  }
  async function runAction(action, parameters = {}) {
    if (!actions.has(action) || preview || !state.verified || state.busy || (state.uncertain && !["pause", "kill"].includes(action))) return;
    if (!window.crypto?.randomUUID) { notice("Secure request identifiers are unavailable. Use the HTTPS Telegram Mini App.", true); return; }
    state.busy = true; updateButtons(); const requestId = crypto.randomUUID();
    try { const result = await api(action, {request_id: requestId, ...parameters}); if (result.status === "confirmation_required") openConfirmation(result, action, parameters, requestId); else { outcome(result); await load("dashboard"); if (state.section !== "dashboard") await load(state.section); } }
    catch (error) { if (error.message.includes("uncertain") || error.message.includes("unknown")) state.uncertain = true; notice("Owner action denied / unknown: " + human(error.message) + ". Do not blindly retry a possibly committed action.", true); }
    finally { state.busy = false; updateButtons(); }
  }
  async function cancelConfirmation() {
    if (state.busy) return;
    const pending = state.pending; state.pending = null; clearInterval(expiryTimer); $("confirmation-dialog").close();
    if (pending && state.verified && !preview) { try { await api("cancel_confirmation", {confirmation_token: pending.token}); } catch { notice("Confirmation canceled locally; server expiry still applies. No trade retry was requested."); } }
  }
  async function confirmAction() {
    const pending = state.pending; if (!pending || state.busy || !$("confirmation-check").checked || Date.now() >= pending.expires || preview) return;
    state.busy = true; $("confirm-action").disabled = true; updateButtons();
    try { const result = await api(pending.action, {request_id: pending.requestId, ...pending.parameters, confirmation_token: pending.token}); state.pending = null; clearInterval(expiryTimer); $("confirmation-dialog").close(); outcome(result); await load("dashboard"); await load(state.section); }
    catch (error) { if (error.message.includes("uncertain") || error.message.includes("unknown")) state.uncertain = true; state.pending = null; clearInterval(expiryTimer); $("confirmation-dialog").close(); notice("Confirmation denied / unknown: " + human(error.message) + ". Do not blindly retry; inspect the stored outcome and reconcile.", true); }
    finally { state.busy = false; updateButtons(); }
  }

  for (const node of document.querySelectorAll("[data-section]")) node.addEventListener("click", () => navigate(node.dataset.section));
  const toggleMenu = () => { const open = $("sidebar").classList.toggle("open"); $("menu-toggle").setAttribute("aria-expanded", String(open)); };
  $("menu-toggle").onclick = toggleMenu; $("mobile-more").onclick = toggleMenu;
  $("refresh-button").onclick = async () => { if (!state.verified && !preview) return; try { await load("dashboard"); await load(state.section); } catch { notice("Stored data is unavailable. Unknown is not clearance.", true); } };
  $("pause-button").onclick = () => runAction("pause"); $("resume-button").onclick = () => runAction("resume"); $("kill-button").onclick = () => runAction("kill"); $("close-all-button").onclick = () => runAction("close_all");
  $("fallback-block-button").onclick = () => runAction("ai_fallback", {mode: "BLOCK_ON_AI_FAILURE"});
  $("fallback-technical-button").onclick = () => runAction("ai_fallback", {mode: "TECHNICAL_ONLY"});
  $("ai-reset-button").onclick = () => runAction("ai_reset"); $("ack-all-button").onclick = () => runAction("alerts/ack");
  for (const node of document.querySelectorAll("[data-alert-level]")) node.onclick = async () => { state.alertLevel = node.dataset.alertLevel; if (preview) { renderAlerts(); return; } try { await load("alerts"); } catch { notice("Stored alerts are unavailable.", true); } };
  $("cancel-confirmation").onclick = cancelConfirmation; $("confirm-action").onclick = confirmAction; $("confirmation-check").onchange = () => { $("confirm-action").disabled = !state.pending || state.busy || !$("confirmation-check").checked || Date.now() >= state.pending.expires; };
  $("confirmation-dialog").addEventListener("cancel", (event) => { event.preventDefault(); cancelConfirmation(); });
  for (const node of document.querySelectorAll("[data-curve-range]")) node.onclick = () => { state.curveRange = node.dataset.curveRange; for (const button of document.querySelectorAll("[data-curve-range]")) button.classList.toggle("selected", button === node); renderCurve(state.data.dashboard?.equity_curve || []); };

  async function boot() {
    if (preview) {
      $("preview-banner").hidden = false; put("session-label", "Synthetic · read-only"); put("owner-access-caption", "No identity · UI fixture");
      try { const data = window.__REFLEX_UI_FIXTURE__ || await (await fetch("/preview/data", {credentials: "omit", cache: "no-store", redirect: "error"})).json(); if (data.preview_only !== true) throw new Error("not_fixture"); state.data = data; $("locked-view").hidden = true; $("authenticated-view").hidden = false; for (const section of Object.keys(names)) renderSection(section); await navigate("dashboard"); }
      catch { locked("The explicit synthetic UI fixture is unavailable. No owner identity fallback exists."); }
      return;
    }
    if (window.location.protocol !== "https:") {
      locked("HTTPS is required for the owner interface. No signed owner data will be sent over a public HTTP origin."); return;
    }
    const telegram = window.Telegram?.WebApp;
    if (!telegram?.initData) { locked("Open this Mini App from your configured owner's private Telegram bot. The backend verifies the original signed initData; browser user IDs and local credentials are never accepted."); return; }
    signedInitData = telegram.initData; state.verified = true;
    try {
      // First successful backend response, not client initDataUnsafe, establishes access.
      state.data.dashboard = await api("dashboard");
      telegram.ready(); telegram.expand();
      $("locked-view").hidden = true; $("authenticated-view").hidden = false; put("session-label", "Verified by backend"); put("owner-access-caption", "Verified Telegram owner");
      for (const section of ["settings", "positions", "news", "suggestions"]) await load(section);
      renderDashboard(); await navigate("dashboard");
      clearInterval(refreshTimer); refreshTimer = setInterval(async () => { if (!state.verified || document.hidden || state.busy || state.pending) return; try { if (state.section !== "alerts") await load(state.section); } catch { notice("Current stored data is unavailable; no trading clearance is inferred.", true); } }, 20000);
      try { await load("alerts"); } catch { put("nav-alert-count", "?"); }
      clearInterval(alertTimer); alertTimer = setInterval(async () => { if (!state.verified || document.hidden || state.busy || state.pending) return; try { await load("alerts"); } catch { put("nav-alert-count", "?"); } }, 30000);
    } catch { if (state.verified) locked("Owner authentication or the guarded service is unavailable. Reopen from Telegram; there is no client-created session renewal."); }
  }
  if (preview) boot();
  else if (window.Telegram?.WebApp) boot();
  else { window.addEventListener("reflex-telegram-sdk-ready", boot, {once: true}); window.addEventListener("reflex-telegram-sdk-failed", () => locked("Telegram SDK unavailable. Open from the private owner bot after restoring HTTPS/network access."), {once: true}); }
})();
