**PROMPT FOR CODEX**

You are refactoring the **saxo-daytrader** application[](https://github.com/simonellefsen/saxo-daytrader) into a **disciplined, opportunistic swing / day trader**.

**Current state (from provided screenshots + SKILL.md):**
- Modern web frontend over an existing Python trading runtime with full Saxo OpenAPI adapter.
- Tabs: Portfolio, Performance, Market Status, Watchlist, **Decision Report**, Execution.
- Decision Report already contains xAI report + deterministic strategy selection, selected assets, suggested trades (with priority/confidence/rationale), strategy flow, and Report JSON.
- Watchlist is quote-ranked per region (Nordics, UK, US, EU/Euronext) with daily leaders/laggards.
- Portfolio enforces local budget model and ladder status.
- Execution queue, broker sync, live orders, Saxo precheck/placement, and bracket/ladder orders are fully functional.
- Full Saxo OpenAPI integration rules are documented in `SKILL.md` (OAuth, instrument lookup, tick-size normalization, precheck, order payloads, reconciliation, sim/live separation, etc.). **Always follow SKILL.md exactly**.

**New trading philosophy (replace current aggressive ladder logic):**
Act as a **disciplined, opportunistic swing/day trader** that:
- Takes only high-conviction, liquid, news-sensitive names.
- Maintains clean, concentrated portfolios.
- Avoids low-conviction diversification.
- Only holds names you would genuinely want to own tomorrow morning.

**HARD RULES — enforce these at every decision step:**
- Total holdings: **10–25** positions (inclusive).
- Every individual holding weight: **5–25 %** of total portfolio equity.
- Only trade securities that are present in the **current Watchlist**.
- **Never trade** these symbols under any circumstances (even if already in portfolio):
  - `NOVOb:xcse`
  - `TSLA:xnas`
- Prefer highly liquid, news-catalyst-driven names on Nordic, EU/Euronext, UK, and US exchanges.
- Return **only** holdings you would actually want to own tomorrow morning.

**Daily analysis & decision schedule (timezone-aware with DST handling):**

The system **must** be fully aware of Daylight Saving Time (DST) crossovers between Europe (CET ↔ CEST) and the United States (EST ↔ EDT). Use Python’s `zoneinfo` (preferred) or `pytz` for accurate timezone calculations so that market-close triggers remain correct year-round without manual intervention.

Run the following automated analyses every trading day:

1. **08:00 CET/CEST — Morning Macro + Asia Pulse**
   - Pull latest data on Asian markets (Shanghai SSE, Tokyo TSE, NSE India, Hong Kong HKEX, Shenzhen, Taiwan).
   - Scan key news sources: geo-political developments, major financial news, earnings calendar, commodities futures (oil, gas, gold, silver).
   - Use AI API (optimize for **as few prompts as possible** — ideally 1–2 well-crafted structured prompts) to synthesize a macro sentiment.
   - Apply this view to the entire Watchlist + current Portfolio.
   - Output per-symbol sentiment using exactly this scale: **SELL, UNDERWEIGHT, HOLD, OVERWEIGHT, BUY**.
   - Store full reasoning in the Decision Report.

2. **≈ 2 hours before Nordic/EU close — Pre-EU/Nordic Close (US Open Focus)**
   - Automatically calculate Nordic/EU market close (normally 17:00 local CET/CEST).
   - Heavy focus on US market setup (futures, upcoming earnings, macro data).
   - Re-run sentiment scoring on Watchlist + Portfolio with fresh US-open lens.
   - Generate actionable trade suggestions (buy/sell/rebalance) that respect all hard portfolio rules.

3. **≈ 1 hour before US close — Pre-US Close Final Assessment**
   - Automatically calculate US market close (16:00 ET / EST or EDT).
   - Produce the definitive set of suggested trades for the next session (or same-day execution if still possible).
   - Update Decision Report with latest JSON and rationale.

The scheduler must use timezone-aware logic so the three daily pulses always fire at the correct local times regardless of DST transitions.

**Decision Report enhancements (single source of truth):**
- Keep the existing UI cards (Created, Status, Selected Assets, Suggested Trades, Report Cadence, Cash Buffer, Next Planned Report, Strategy Flow, Strategy Status, Report JSON).
- Update “Strategy Flow” to reflect the new swing-oriented logic (macro → sentiment → portfolio constraints → high-conviction trades).
- “Suggested Trades” table must include: SYMBOL, ACTION (BUY/SELL/FLATTEN), PRIORITY (high/medium), CONFIDENCE (0–100), RATIONALE (concise, news/macro driven).
- After each analysis, automatically generate the next planned trades that feed directly into the Execution tab.

**Trade execution style (new swing-oriented):**
- Primary style: **opportunistic swing entries** with clear thesis and defined risk (stop-loss).
- Use ladders only when the strategy explicitly calls for scaling — never as the default aggressive mechanism.
- Every suggested trade must respect portfolio weight rules (calculate target allocation before proposing size).
- On SELL/UNDERWEIGHT: flatten or reduce to target weight.
- On BUY/OVERWEIGHT: add to reach target weight (5–25 %).
- Preserve all existing Saxo order capabilities (precheck, bracket orders, tick normalization, etc.) but route them through the new disciplined decision engine.

**Technical integration requirements:**
- Reuse/extend the existing Decision Report logic (xAI + deterministic selection pipeline).
- Keep the Watchlist as the sole universe — never suggest anything outside it.
- Consult Portfolio Snapshot and local budget model before any trade suggestion.
- All Saxo interactions **must** follow SKILL.md exactly.
- Scheduler should trigger the three daily analyses at the exact timezone-aware times (make them configurable via env/config).
- Minimize AI API calls — design prompts that do macro synthesis + per-symbol sentiment in as few calls as possible (return structured JSON).
- Preserve all existing UI tabs and functionality; only enhance Decision Report and the underlying strategy engine.
- Add clear logging and “Last Analysis” timestamps (with timezone) so the user always sees when each macro pulse ran.

**Additional best-practice guardrails:**
- Risk management: never exceed 25 % in any single name.
- Respect the existing 10 % cash buffer logic.
- Hard-coded blacklist enforcement at every decision step.
- Full auditability: every decision traceable to macro inputs + AI sentiment + portfolio math.

Implement this new disciplined swing/day-trader strategy by refactoring the core decision engine while keeping the rest of the application (Saxo adapter, frontend, execution queue, etc.) intact and fully functional.

Start by outlining the new modules/files you will create or modify, then proceed with the code changes.
