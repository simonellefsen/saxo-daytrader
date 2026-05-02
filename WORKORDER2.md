# Work Order / Task List for Codex  
**Saxo Connection Status + UI/UX Polish (Kubernetes Edition)**  
**Project:** saxo-daytrader-xai (FastAPI Backend + Next.js Frontend)

**Version:** 2.0  
**Date:** April 2026  
**Goal:** Add clear, real-time visibility into the Saxo API broker connection status (token validity, expiry, SIM vs LIVE account) and apply targeted UI/UX improvements based on the latest screenshots.

---

## 1. New Feature: Saxo Connection Status (Highest Priority)

### Requirements
Add a prominent, always-visible **Saxo Connection Status** indicator that shows:
- **Connection health**: Green (valid & healthy), Yellow (expiring soon, < 10 min), Red (expired / needs re-auth).
- **Environment**: Clear badge “SIM” (blue) or “LIVE” (green/red) based on `SAXO_ENVIRONMENT`.
- **Token info** (on hover or click): “Expires in XX minutes”, last refreshed timestamp, refresh status.
- **Action button**: “Re-authenticate” that triggers the frontend-initiated OAuth flow (from previous Work Order v1.7).

### Placement
- **Top bar** (next to “Environment: kubernetes” and user avatar) — most visible spot.
- **Execution tab** — add a dedicated “Saxo Broker Status” card at the top with detailed info (token expiry countdown, last successful refresh, refresh token status).
- **WebSocket / polling**: Real-time updates via existing WebSocket (or new `/api/saxo/auth/status` endpoint).

### Backend support (reuse/enhance SaxoAuthManager)
- Extend `/api/saxo/auth/status` to return:
  ```json
  {
    "connected": true,
    "environment": "sim" | "live",
    "token_valid": true,
    "expires_at": "2026-04-26T15:00:00Z",
    "expires_in_minutes": 47,
    "refreshing": false,
    "needs_reauth": false
  }

--

## 2. UI/UX Improvements Based on Latest Screenshots

1. Cash Buffer Warning Banner
 - Make the yellow banner text clickable or add two action buttons:
  - “Add cash” (placeholder for now)
  - “Reduce exposure” → opens a modal with current deployment % and quick-adjust sliders.


2. Decision Report Tab
 - When status = “failed” or “no_scored_candidates”: show a more user-friendly message + suggested next action (e.g. “No tradable candidates — waiting for next analysis window” or “xAI call failed — check logs”).
 - Make the Report JSON block collapsible with syntax highlighting.

3. Portfolio Snapshot Table
 - Add sorting by Unrealised P/L or Allocation %.
 - Improve LADDER STATUS column to show a small progress indicator (e.g. “3/5 rungs filled”).

4. Performance Tab
 - Add a small legend explaining the green (Portfolio) and blue (Cash) lines.

5. Execution Tab
 - Add small green/red status dots to the cycle cards (“Cycle #182 ok”).
 - Show “Daily executed-trade cap” as a progress bar (0/25).

6. General Polish (apply everywhere)
 - Global “Last updated: XX seconds ago” with live countdown.
 - Consistent Danish number formatting (kr. with correct separators).
 - Add subtle loading spinners on all action buttons (Run Queue Processor, Sync Broker Status, etc.).
 - In Market Status tab: highlight today’s row.

--

## 3. Technical Tasks for Codex

### Backend (FastAPI)

- Enhance SaxoAuthManager to expose full status via the existing /api/saxo/auth/status endpoint.
- Ensure WebSocket broadcasts connection changes instantly.
- Add clear logging for token refresh events.

### Frontend (Next.js)

- Update top bar with new Saxo status component.
- Add detailed status card in Execution tab.
- Integrate with existing WebSocket for live updates.
- Implement the Re-authenticate flow.

### Keep Existing Features Intact

- Do not break current Ladder Visualizer, Performance chart, Decision Report JSON, Portfolio Snapshot, etc.
- Maintain the clean, modern look shown in the screenshots.

--

#### Ready for Codex
Copy this entire Work Order and paste it into ChatGPT 5.4 Codex with the prompt:
“Implement Work Order v2.0 exactly in the saxo-daytrader-xai project. Add the Saxo Connection Status indicator (token validity, expiry, SIM/LIVE) in the top bar and Execution tab, plus all the UI/UX improvements listed. Reuse the existing SaxoAuthManager and WebSocket infrastructure.”
