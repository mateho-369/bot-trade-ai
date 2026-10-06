# Native Windows/MT5 validation checklist — not executed by repository tests

This checklist is for a separately authorized, supervised Windows DEMO environment. Empty boxes are
unverified requirements. Synthetic/mock tests are not evidence of native terminal access, broker behavior,
profitability, stage eligibility or LIVE permission. This release refuses LIVE startup.

## Source and host

- [ ] Verify the exact release manifest against an independently trusted digest and inspect source.
- [ ] Install Python 3.11 x64 and pinned dependencies on a dedicated supported Windows host.
- [ ] Review NTFS ACLs for source, `.env`, database, runtime marker, reports, logs and backups. Keep the
      terminal and runtime under a limited interactive user; no Session 0 service.
- [ ] Verify vendor origin/signature and exact path of `terminal64.exe`; confirm OS patching, storage,
      clock, time zone, sleep/hibernation, login/RDP and reboot behavior.

## DEMO identity and configuration

- [ ] Keep `LIVE_TRADING=false`, `START_PAUSED=true`, `AUTONOMOUS_DEMO=true`,
      `AI_REQUIRE_APPROVAL=true` and `DEMO_FAST_TRACK=false` in the reviewed `.env`.
- [ ] Log MT5 into the intended DEMO account. Run the read-only account checker and confirm the terminal
      itself reports DEMO, expected server/currency and a deliberate login.
- [ ] Confirm resolved native symbol names, tick size/value, contract size, min/step/max volume, spread,
      margin, stop/freeze levels, commission, swap and broker-side SL/TP behavior for every symbol.
- [ ] Verify news/calendar source coverage and entitlement; missing/unknown required coverage must block
      new entries.

## Startup and controlled DEMO behavior

- [ ] Run `scripts\setup_demo.ps1`; first pass should create `.venv` and `.env.demo.example` copy then
      exit. Review `.env`; second pass must complete checks without prompting.
- [ ] Run `start_demo.bat` only once. Confirm runtime is PAUSED until reconciliation/components/AI-health
      and every resume gate pass. Confirm no unknown order is resent and no risk/latch history is reset.
- [ ] Exercise explicit local `pause`, `resume`, `kill`, `stop`, and reviewed `clear-stop` procedures with
      the CLI and verify durable audit/report state. No Telegram command/API should change control state.
- [ ] Verify critical-alert auto-pause, the 15-minute cooldown, all gates, maximum three automatic
      resumes per UTC runtime day and continued pause after cap.
- [ ] Confirm open positions retain broker-side SL/TP during pause/stop; local trailing is unavailable
      while stopped. Test broker disconnect, stale quote/news, spread, rejection, persistence failure,
      unknown acknowledgement and unsettled intent as fail-closed conditions.
- [ ] Verify watchdog never force-kills a live child or starts a replacement until the prior child exits;
      replacement follows the same PAUSED/reconcile path.

## Reports, recovery and operations

- [ ] Confirm local `data\reports\actions.log` and daily JSONL are private and updated even with Telegram
      settings blank/invalid. If configured, observe only outbound HTTPS `sendMessage` reporting.
- [ ] Verify logs/console contain no raw provider/SDK exceptions, credentials, account identifiers or
      request bodies. Confirm no listener, polling task, webhook, Mini App, keyboard or inbound command.
- [ ] Perform supervised backup/restore and inspect SQLite WAL/sidecar behavior without deleting or
      copying around a nonempty WAL or uncertain financial write.
- [ ] Record actual Windows version, terminal/account type, symbol specifications, source hashes,
      configuration hash, test evidence and all deviations before any later stage review.

A completed checklist is still not strategy-profitability evidence or LIVE authorization. LIVE is outside
this release's scope.
