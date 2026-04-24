"use client";

import { useEffect, useMemo, useState } from "react";

import useSWR, { mutate } from "swr";

import { apiFetch, getFetcher, postAction } from "@/lib/api";
import { formatDkk, formatLocalMoney, formatNumber, formatPercent, formatTimestamp, formatTimestampPrecise, signedClass, toYahooFinanceUrl } from "@/lib/format";
import type {
  AssetLadderHistoryResponse,
  DecisionHistoryResponse,
  DecisionResponse,
  ExecutionResponse,
  MarketResponse,
  OverviewResponse,
  PerformanceResponse,
  PositionsResponse,
  SchedulerResponse,
} from "@/lib/types";
import { LadderVisualizer } from "@/components/ladder-visualizer";
import { LineChart } from "@/components/line-chart";
import { Sparkline } from "@/components/sparkline";

type TabKey = "portfolio" | "performance" | "market" | "decision" | "execution";

const TAB_OPTIONS: Array<{ key: TabKey; label: string }> = [
  { key: "portfolio", label: "Portfolio" },
  { key: "performance", label: "Performance" },
  { key: "market", label: "Market Status" },
  { key: "decision", label: "Decision Report" },
  { key: "execution", label: "Execution" },
];

const PERFORMANCE_RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"] as const;

const PORTFOLIO_COLUMN_HELP: Record<string, string> = {
  Symbol: "Trading symbol. Click to open the instrument on Yahoo Finance.",
  Instrument: "Instrument or company name.",
  "Ladder Status": "Current ladder strategy state for the symbol.",
  Trend: "Short intraday sparkline from the recent chart window.",
  Qty: "Current broker-aligned quantity held.",
  Currency: "Trading currency of the instrument.",
  "Paid Price": "Average price paid per unit in the instrument currency.",
  "Current Price": "Latest polled market price in the instrument currency.",
  "Cost Basis DKK": "Current remaining acquisition cost in DKK for the held quantity.",
  "Market Value DKK": "Current position value in DKK using the latest price and FX rate.",
  "Unrealised P/L DKK": "Unrealised profit or loss in DKK before tax.",
  "FX Gain/Loss DKK": "Part of unrealised P/L caused by FX movement since purchase.",
  "Daily P/L DKK": "Change in DKK since the 06:00 Copenhagen intraday baseline.",
  Allocation: "Share of total portfolio value currently allocated to this position.",
  "Quote Updated": "Timestamp of the latest stored quote used for this row.",
};

function PortfolioRow({
  row,
  onOpen,
}: {
  row: Record<string, any>;
  onOpen: (symbol: string) => void;
}) {
  const symbol = String(row.symbol ?? "");
  const sparkline = useSWR<AssetLadderHistoryResponse>(
    `/api/ladder-chart/${encodeURIComponent(symbol)}?range_key=1H`,
    getFetcher,
    { refreshInterval: 120_000 },
  );
  const sparkValues = (sparkline.data?.chart?.points ?? []).map((point) => Number(point.close ?? 0)).filter((value) => Number.isFinite(value) && value > 0);
  const positive = sparkValues.length > 1 ? sparkValues[sparkValues.length - 1] >= sparkValues[0] : Number(row.daily_pnl_dkk ?? 0) >= 0;

  return (
    <tr className="clickable-row" key={symbol} onClick={() => onOpen(symbol)}>
      <td>
        <a
          href={toYahooFinanceUrl(symbol)}
          target="_blank"
          rel="noreferrer"
          onClick={(event) => event.stopPropagation()}
        >
          {symbol}
        </a>
      </td>
      <td>{String(row.instrument_name ?? symbol)}</td>
      <td>
        <div className="ladder-status-cell">
          <span className={`status-chip ${row.ladder_status?.trailing ? "good" : "neutral"}`}>
            {String(row.ladder_status?.text ?? "idle")}
          </span>
          {Number(row.ladder_status?.progress_pct ?? 0) > 0 ? (
            <div className="ladder-progress">
              <div className="ladder-progress-bar" style={{ width: `${Math.max(6, Math.round(Number(row.ladder_status?.progress_pct ?? 0) * 100))}%` }} />
            </div>
          ) : null}
        </div>
      </td>
      <td>
        <button
          className="sparkline-button"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(symbol);
          }}
          aria-label={`Open ladder visualizer for ${symbol}`}
        >
          <Sparkline values={sparkValues} positive={positive} />
        </button>
      </td>
      <td>{formatNumber(row.quantity, 0)}</td>
      <td>{String(row.currency ?? "")}</td>
      <td>
        {row.paid_price_local === null || row.paid_price_local === undefined
          ? "n/a"
          : formatLocalMoney(row.paid_price_local, row.currency)}
      </td>
      <td>{formatLocalMoney(row.current_price_local, row.currency)}</td>
      <td>{formatDkk(row.cost_basis_dkk)}</td>
      <td>{formatDkk(row.market_value_dkk)}</td>
      <td className={signedClass(row.unrealised_pnl_dkk)}>{formatDkk(row.unrealised_pnl_dkk)}</td>
      <td className={signedClass(row.fx_unrealised_pnl_dkk)}>
        {row.fx_unrealised_pnl_dkk === null || row.fx_unrealised_pnl_dkk === undefined
          ? "n/a"
          : formatDkk(row.fx_unrealised_pnl_dkk)}
      </td>
      <td className={signedClass(row.daily_pnl_dkk)}>{formatDkk(row.daily_pnl_dkk)}</td>
      <td>{formatPercent(row.allocation_pct)}</td>
      <td>{formatTimestamp(row.latest_quote_updated_at)}</td>
    </tr>
  );
}

function metricSubvalue(value: unknown, formatter: (value: unknown) => string) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  return formatter(value);
}

export function DashboardShell() {
  const [activeTab, setActiveTab] = useState<TabKey>("portfolio");
  const [performanceRange, setPerformanceRange] = useState<(typeof PERFORMANCE_RANGES)[number]>("1D");
  const [selectedDecisionId, setSelectedDecisionId] = useState<number | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("");
  const [statusTone, setStatusTone] = useState<"info" | "warn" | "good">("info");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("daytrader-active-tab") as TabKey | null;
    if (stored && TAB_OPTIONS.some((tab) => tab.key === stored)) {
      setActiveTab(stored);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("daytrader-active-tab", activeTab);
  }, [activeTab]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName ?? "";
      if (target?.isContentEditable || tagName === "INPUT" || tagName === "TEXTAREA") {
        return;
      }
      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        void runAction("/api/actions/scheduler-cycle", { mock: false });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const overview = useSWR<OverviewResponse>("/api/overview", getFetcher, {
    refreshInterval: 15_000,
  });

  useEffect(() => {
    if (overview.data) {
      setLastUpdatedAt(new Date().toISOString());
    }
  }, [overview.data]);

  const priceRefreshMs = Math.max(
    15_000,
    Number(overview.data?.refresh?.price_poll_interval_minutes ?? 1) * 60_000,
  );

  const positions = useSWR<PositionsResponse>(
    activeTab === "portfolio" ? "/api/portfolio/positions?limit=25" : null,
    getFetcher,
    { refreshInterval: priceRefreshMs },
  );
  const performance = useSWR<PerformanceResponse>(
    activeTab === "performance" ? `/api/performance?range_key=${performanceRange}` : null,
    getFetcher,
    { refreshInterval: priceRefreshMs },
  );
  const market = useSWR<MarketResponse>(
    activeTab === "market" ? "/api/market/status" : null,
    getFetcher,
    { refreshInterval: 60_000 },
  );
  const decision = useSWR<DecisionResponse>(
    activeTab === "decision" ? "/api/decision/latest" : null,
    getFetcher,
    { refreshInterval: 60_000 },
  );
  const decisionHistory = useSWR<DecisionHistoryResponse>(
    activeTab === "decision" ? "/api/decision/reports?limit=20" : null,
    getFetcher,
    { refreshInterval: 60_000 },
  );
  const execution = useSWR<ExecutionResponse>(
    activeTab === "execution" ? "/api/execution?limit=150" : null,
    getFetcher,
    { refreshInterval: 15_000 },
  );
  const scheduler = useSWR<SchedulerResponse>(
    activeTab === "execution" ? "/api/scheduler?limit=10" : null,
    getFetcher,
    { refreshInterval: 30_000 },
  );

  async function runAction(path: string, body?: unknown) {
    setPendingAction(path);
    try {
      const result = await postAction<Record<string, unknown>>(path, body);
      setStatusTone("good");
      setStatusMessage(`Action completed: ${JSON.stringify(result)}`);
      await Promise.all([
        mutate("/api/overview"),
        mutate("/api/portfolio/positions?limit=25"),
        mutate(`/api/performance?range_key=${performanceRange}`),
        mutate("/api/execution?limit=150"),
        mutate("/api/decision/latest"),
        mutate("/api/decision/reports?limit=20"),
        mutate("/api/scheduler?limit=10"),
      ]);
    } catch (error) {
      setStatusTone("warn");
      setStatusMessage(error instanceof Error ? error.message : "Action failed.");
    } finally {
      setPendingAction(null);
    }
  }

  const summary = overview.data?.portfolio_summary ?? {};
  const afterTaxSummary = overview.data?.after_tax_summary ?? {};
  const integrityWarnings = overview.data?.integrity?.warnings ?? [];
  const analysisSummary = overview.data?.analysis_summary;

  const performanceSeries = useMemo(() => {
    return (performance.data?.history ?? []).map((row) => Number(row.total_market_value_dkk ?? 0));
  }, [performance.data?.history]);

  const browserTimeZone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "local time", []);

  const portfolioColumns = useMemo(
    () => [
      "Symbol",
      "Instrument",
      "Ladder Status",
      "Trend",
      "Qty",
      "Currency",
      "Paid Price",
      "Current Price",
      "Cost Basis DKK",
      "Market Value DKK",
      "Unrealised P/L DKK",
      "FX Gain/Loss DKK",
      "Daily P/L DKK",
      "Allocation",
      "Quote Updated",
    ],
    [],
  );

  const latestDecision = decision.data?.report;
  const nextDecision = decision.data?.next_report ?? null;
  const decisionHistoryItems = decisionHistory.data?.items ?? [];
  const displayedDecision = useMemo(() => {
    if (selectedDecisionId !== null) {
      const selected = decisionHistoryItems.find((row) => Number(row.id) === selectedDecisionId);
      if (selected) {
        return selected;
      }
    }
    return latestDecision ?? decisionHistoryItems[0] ?? null;
  }, [decisionHistoryItems, latestDecision, selectedDecisionId]);
  const decisionSuggestions = Array.isArray(displayedDecision?.report_json?.suggested_trades)
    ? (displayedDecision?.report_json?.suggested_trades as Array<Record<string, unknown>>)
    : [];
  const selectedAssets = Array.isArray(displayedDecision?.report_json?.strategy_plan?.selected_assets)
    ? (displayedDecision?.report_json?.strategy_plan?.selected_assets as Array<Record<string, unknown>>)
    : [];
  const strategyPlan = (displayedDecision?.report_json?.strategy_plan ?? {}) as Record<string, unknown>;
  const cashManagement = (displayedDecision?.report_json?.cash_management ?? {}) as Record<string, unknown>;

  useEffect(() => {
    if (!decisionHistoryItems.length && !latestDecision) {
      setSelectedDecisionId(null);
      return;
    }
    if (selectedDecisionId === null) {
      const fallbackId = Number(latestDecision?.id ?? decisionHistoryItems[0]?.id ?? 0);
      setSelectedDecisionId(fallbackId || null);
      return;
    }
    const existsInHistory = decisionHistoryItems.some((row) => Number(row.id) === selectedDecisionId);
    if (!existsInHistory && Number(latestDecision?.id) !== selectedDecisionId) {
      const fallbackId = Number(latestDecision?.id ?? decisionHistoryItems[0]?.id ?? 0);
      setSelectedDecisionId(fallbackId || null);
    }
  }, [decisionHistoryItems, latestDecision, selectedDecisionId]);

  const executionOrders = execution.data?.orders ?? [];
  const manageableOrders = executionOrders.filter((row) =>
    [
      "submitted_to_broker",
      "broker_working",
      "broker_amended",
      "broker_partially_filled",
      "broker_replace_requested",
      "broker_cancel_requested",
    ].includes(String(row.status ?? "")),
  );

  const ladderSummary = useMemo(() => {
    const ladderOrders = executionOrders.filter((row) => String(row.strategy_type ?? "") === "ladder");
    const activeLadders = new Set(
      ladderOrders
        .filter((row) =>
          [
            "pending_execution",
            "pending_approval",
            "waiting_for_market_open",
            "submitted_to_broker",
            "broker_working",
            "broker_amended",
            "broker_partially_filled",
            "broker_replace_requested",
            "broker_cancel_requested",
          ].includes(String(row.status ?? "")),
        )
        .map((row) => String(row.symbol ?? "")),
    ).size;
    const filledRungs = ladderOrders.filter((row) => String(row.strategy_role ?? "") === "entry" && String(row.status ?? "") === "executed").length;
    return { activeLadders, filledRungs };
  }, [executionOrders]);

  return (
    <main className="shell">
      <header className="page-header">
        <div className="title-block">
          <h1>{overview.data?.app?.project_name ?? "saxo-daytrader-xai"}</h1>
          <p>
            Modern web frontend over the existing Python trading runtime. Targeted polling keeps the active
            view fresh without re-running the whole page.
          </p>
          <p className="muted">Last updated {formatTimestampPrecise(lastUpdatedAt)} · Shortcut: R runs one scheduler cycle</p>
        </div>
        <div className="pill-row">
          <span className="pill">Execution: {String(overview.data?.execution?.mode ?? "n/a").toUpperCase()}</span>
          <span className="pill">Adapter: {overview.data?.execution?.adapter ?? "n/a"}</span>
          <span className="pill">Environment: {overview.data?.app?.environment ?? "n/a"}</span>
        </div>
      </header>

      {integrityWarnings.map((warning) => (
        <section className="banner warn" key={warning}>
          {warning}
        </section>
      ))}

      {Number(cashManagement.cash_buffer_shortfall_dkk ?? 0) > 0 ? (
        <section className="banner warn">
          Cash buffer is below target by {formatDkk(cashManagement.cash_buffer_shortfall_dkk)}. Add cash or reduce exposure.
        </section>
      ) : null}

      {analysisSummary?.analysis_window_active ? (
        <section className="banner good">
          Analysis window active for {analysisSummary.active_windows.join(", ")}.
        </section>
      ) : analysisSummary?.pre_sync_markets?.length ? (
        <section className="banner info">
          Pre-analysis broker sync window active for {analysisSummary.pre_sync_markets.join(", ")}.
        </section>
      ) : (
        <section className="banner info">Analysis window inactive right now.</section>
      )}

      <section className="metric-grid">
        <article className="metric-card">
          <div className="label">Portfolio Value</div>
          <div className="value">{formatDkk(summary.total_market_value_dkk)}</div>
          <div className="subvalue">Invested {formatDkk(summary.invested_market_value_dkk)}</div>
        </article>
        <article className="metric-card">
          <div className="label">Cash</div>
          <div className="value">{formatDkk(summary.cash_balance_dkk)}</div>
          <div className="subvalue">
            Initial {formatDkk(summary.initial_cash_dkk)} · Trades {formatDkk(summary.cash_from_trades_dkk)}
          </div>
        </article>
        <article className="metric-card">
          <div className="label">Unrealised P/L</div>
          <div className={`value ${signedClass(summary.total_unrealised_pnl_dkk)}`}>
            {formatDkk(summary.total_unrealised_pnl_dkk)}
          </div>
          <div className="subvalue">
            After tax {formatDkk(afterTaxSummary.after_tax_unrealised_pnl_dkk)}
          </div>
        </article>
        <article className="metric-card">
          <div className="label">Daily P/L Since 06:00</div>
          <div className={`value ${signedClass(summary.total_daily_pnl_dkk)}`}>
            {formatDkk(summary.total_daily_pnl_dkk)}
          </div>
          <div className="subvalue">{formatNumber(summary.position_count, 0)} positions</div>
        </article>
      </section>

      <nav className="tabs" aria-label="Primary dashboard tabs">
        {TAB_OPTIONS.map((tab) => (
          <button
            key={tab.key}
            className={`tab ${activeTab === tab.key ? "active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {statusMessage ? (
        <section className={`banner ${statusTone}`}>
          {statusMessage}
        </section>
      ) : null}

      {activeTab === "portfolio" ? (
        <section className="panel stack">
          <div className="panel-header">
            <div>
              <h2>Portfolio Snapshot</h2>
              <p>Broker-aligned live holdings with a capped local budget model for new buys.</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {portfolioColumns.map((column) => (
                    <th key={column}>
                      <span className="help-header" title={PORTFOLIO_COLUMN_HELP[column]}>
                        {column}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(positions.data?.items ?? []).map((row) => (
                  <PortfolioRow key={String(row.symbol ?? "")} row={row} onOpen={setSelectedSymbol} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {activeTab === "performance" ? (
        <section className="panel stack">
          <div className="panel-header">
            <div>
              <h2>Performance</h2>
              <p>Portfolio value history and progress against the DKK 500/day before-tax target.</p>
            </div>
            <div className="range-picker">
              {PERFORMANCE_RANGES.map((range) => (
                <button
                  key={range}
                  className={`range-button ${performanceRange === range ? "active" : ""}`}
                  type="button"
                  onClick={() => setPerformanceRange(range)}
                >
                  {range}
                </button>
              ))}
            </div>
          </div>
          <LineChart
            values={performanceSeries}
            positive={(performanceSeries.at(-1) ?? 0) >= (performanceSeries[0] ?? 0)}
          />
          <div className="mini-grid">
            {["day", "week", "month", "year", "all_time"].map((periodKey) => {
              const period = (performance.data?.goal_tracking?.periods?.[periodKey] ?? {}) as Record<string, unknown>;
              return (
                <article className="mini-card" key={periodKey}>
                  <div className="label">{periodKey.replace("_", " ").toUpperCase()}</div>
                  <div className={`value ${signedClass(period.pnl_dkk)}`}>{formatDkk(period.pnl_dkk)}</div>
                  <div className="muted">
                    Target {metricSubvalue(period.target_dkk, formatDkk)} · Gap {metricSubvalue(period.gap_dkk, formatDkk)}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      ) : null}

      {activeTab === "market" ? (
        <section className="panel stack">
          <div className="panel-header">
            <div>
              <h2>Market Status</h2>
              <p>Tradability, analysis windows, and calendar timing for tracked exchanges. Times below are shown in {browserTimeZone}.</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Exchange</th>
                  <th>Status</th>
                  <th>Tradable</th>
                  <th>Session Open ({browserTimeZone})</th>
                  <th>Tradable Close ({browserTimeZone})</th>
                  <th>Pre-Sync</th>
                  <th>Open Window</th>
                  <th>Close Window</th>
                  <th>Next Open ({browserTimeZone})</th>
                </tr>
              </thead>
              <tbody>
                {(market.data?.items ?? []).map((row) => (
                  <tr key={String(row.code)}>
                    <td>{String(row.market ?? row.code)}</td>
                    <td>{String(row.status_reason ?? "")}</td>
                    <td>{row.is_tradable ? "Yes" : "No"}</td>
                    <td>{row.session_open_at_utc ? formatTimestamp(row.session_open_at_utc) : "n/a"}</td>
                    <td>{row.tradable_close_at_utc ? formatTimestamp(row.tradable_close_at_utc) : "n/a"}</td>
                    <td>{row.pre_analysis_sync_active ? "Active" : "No"}</td>
                    <td>{row.open_analysis_window_active ? "Active" : "No"}</td>
                    <td>{row.close_analysis_window_active ? "Active" : "No"}</td>
                    <td>{row.next_open_at_utc ? formatTimestamp(row.next_open_at_utc) : "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {activeTab === "decision" ? (
        <section className="panel stack">
          <div className="panel-header">
            <div>
              <h2>Decision Report</h2>
              <p>Latest xAI report plus deterministic strategy selection output.</p>
            </div>
            <div className="action-row">
              <button className="button" type="button" onClick={() => runAction("/api/actions/decision-report")}>
                Generate Report
              </button>
            </div>
          </div>
          <div className="mini-grid">
            <article className="mini-card">
              <div className="label">Created</div>
              <div className="value">{formatTimestamp(displayedDecision?.created_at)}</div>
            </article>
            <article className="mini-card">
              <div className="label">Status</div>
              <div className="value">{String(displayedDecision?.status ?? "n/a")}</div>
            </article>
            <article className="mini-card">
              <div className="label">Selected Assets</div>
              <div className="value">{formatNumber(selectedAssets.length, 0)}</div>
            </article>
            <article className="mini-card">
              <div className="label">Suggested Trades</div>
              <div className="value">{formatNumber(decisionSuggestions.length, 0)}</div>
            </article>
            <article className="mini-card">
              <div className="label">Report Cadence</div>
              <div className="value">Every {formatNumber(overview.data?.refresh?.decision_interval_minutes ?? 15, 0)} min</div>
              <div className="subvalue">While an analysis window is active</div>
            </article>
            <article className="mini-card">
              <div className="label">Cash Buffer</div>
              <div className={`value ${cashManagement.requires_cash_raise ? "negative" : "positive"}`}>
                {cashManagement.requires_cash_raise ? "Below target" : "Healthy"}
              </div>
              <div className="subvalue">
                Cash {formatDkk(cashManagement.cash_balance_dkk)} · Shortfall {formatDkk(cashManagement.cash_buffer_shortfall_dkk)}
              </div>
            </article>
            <article className="mini-card">
              <div className="label">Next Planned Report</div>
              <div className="value">{formatTimestamp(nextDecision?.next_report_at)}</div>
              <div className="subvalue">{String(nextDecision?.reason ?? "n/a")}</div>
            </article>
          </div>
          <div className="grid-2">
            <div className="stack">
              <div className="mini-card">
                <div className="label">Strategy Flow</div>
                <div className="value">
                  {formatNumber(Array.isArray(displayedDecision?.report_json?.candidate_assets) ? displayedDecision?.report_json?.candidate_assets.length : 0, 0)} → {formatNumber(selectedAssets.length, 0)} → {formatNumber(Array.isArray(strategyPlan.ladder_orders) ? strategyPlan.ladder_orders.length : 0, 0)}
                </div>
                <div className="subvalue">xAI candidates → technically selected → ladder orders</div>
              </div>
              <div className="mini-card">
                <div className="label">Strategy Status</div>
                <div className="value">{String(strategyPlan.status ?? "n/a")}</div>
                <div className="muted">
                  {Array.isArray(strategyPlan.notes) ? (strategyPlan.notes as string[]).join(" ") : "n/a"}
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Action</th>
                      <th>Priority</th>
                      <th>Confidence</th>
                      <th>Rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisionSuggestions.map((row, index) => (
                      <tr key={`${String(row.symbol ?? "symbol")}-${index}`}>
                        <td>{String(row.symbol ?? "")}</td>
                        <td>{String(row.action ?? "")}</td>
                        <td>{String(row.priority ?? "")}</td>
                        <td>{formatNumber(row.confidence, 2)}</td>
                        <td>{String(row.rationale ?? "")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Selected</th>
                      <th>Score</th>
                      <th>Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedAssets.map((row, index) => (
                      <tr key={`${String(row.symbol ?? "asset")}-${index}`}>
                        <td>{String(row.symbol ?? "")}</td>
                        <td>{formatNumber(row.score, 2)}</td>
                        <td>{Array.isArray(row.notes) ? (row.notes as string[]).join(", ") : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Created</th>
                      <th>Status</th>
                      <th>Strategy</th>
                      <th>Selected</th>
                      <th>Trades</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decisionHistoryItems.map((row) => {
                      const reportJson = (row.report_json ?? {}) as Record<string, unknown>;
                      const historyStrategy = (reportJson.strategy_plan ?? {}) as Record<string, unknown>;
                      const historySelected = Array.isArray(historyStrategy.selected_assets)
                        ? historyStrategy.selected_assets.length
                        : 0;
                      const historyTrades = Array.isArray(reportJson.suggested_trades)
                        ? reportJson.suggested_trades.length
                        : 0;
                      const isActive = Number(row.id) === selectedDecisionId;
                      return (
                        <tr
                          key={String(row.id)}
                          className={`history-row ${isActive ? "active" : ""}`}
                          onClick={() => setSelectedDecisionId(Number(row.id))}
                        >
                          <td>{formatTimestamp(row.created_at)}</td>
                          <td>{String(row.status ?? "")}</td>
                          <td>{String(historyStrategy.status ?? "n/a")}</td>
                          <td>{formatNumber(historySelected, 0)}</td>
                          <td>{formatNumber(historyTrades, 0)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
            <details className="json-details" open>
              <summary>Report JSON</summary>
              <pre className="code-block">{JSON.stringify(displayedDecision?.report_json ?? {}, null, 2)}</pre>
            </details>
          </div>
        </section>
      ) : null}

      {activeTab === "execution" ? (
        <section className="panel stack">
          <div className="panel-header">
            <div>
              <h2>Execution</h2>
              <p>Queue control, broker sync, and live order management without page-wide reruns.</p>
            </div>
            <div className="pill-row">
              <span className="pill">Queued {formatNumber(overview.data?.execution?.counts?.queued ?? 0, 0)}</span>
              <span className="pill">Broker Live {formatNumber(overview.data?.execution?.counts?.broker_live ?? 0, 0)}</span>
              <span className="pill">Failed {formatNumber(overview.data?.execution?.counts?.failed ?? 0, 0)}</span>
            </div>
          </div>
          <div className="action-row">
            <button className="button" type="button" disabled={pendingAction !== null} onClick={() => runAction("/api/actions/queue-process")}>
              {pendingAction === "/api/actions/queue-process" ? "Running…" : "▶ Run Queue Processor"}
            </button>
            <button className="ghost-button" type="button" disabled={pendingAction !== null} onClick={() => runAction("/api/actions/sync-broker")}>
              {pendingAction === "/api/actions/sync-broker" ? "Syncing…" : "↻ Sync Broker Status"}
            </button>
            <button className="ghost-button" type="button" disabled={pendingAction !== null} onClick={() => runAction("/api/actions/retry-failed")}>
              {pendingAction === "/api/actions/retry-failed" ? "Retrying…" : "↺ Retry Failed Orders"}
            </button>
            <button className="ghost-button" type="button" disabled={pendingAction !== null} onClick={() => runAction("/api/actions/reconcile-broker")}>
              {pendingAction === "/api/actions/reconcile-broker" ? "Reconciling…" : "≋ Reconcile Portfolio To Saxo"}
            </button>
            <button className="ghost-button" type="button" disabled={pendingAction !== null} onClick={() => runAction("/api/actions/scheduler-cycle", { mock: false })}>
              {pendingAction === "/api/actions/scheduler-cycle" ? "Running…" : "⟳ Run Scheduler Cycle"}
            </button>
          </div>
          <div className="mini-grid">
            <article className="mini-card">
              <div className="label">Active Ladders</div>
              <div className="value">{formatNumber(ladderSummary.activeLadders, 0)}</div>
            </article>
            <article className="mini-card">
              <div className="label">Total Rungs Filled</div>
              <div className="value">{formatNumber(ladderSummary.filledRungs, 0)}</div>
            </article>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Created</th>
                  <th>Symbol</th>
                  <th>Action</th>
                  <th>Strategy</th>
                  <th>Role</th>
                  <th>Order Type</th>
                  <th>Status</th>
                  <th>Qty</th>
                  <th>Price</th>
                  <th>Limit</th>
                  <th>Stop</th>
                  <th>Error</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {executionOrders.map((row) => {
                  const isManageable = manageableOrders.some((order) => order.id === row.id);
                  return (
                    <tr key={String(row.id)} className={`status-row status-${String(row.status ?? "").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`}>
                      <td>{String(row.id)}</td>
                      <td>{formatTimestamp(row.created_at)}</td>
                      <td>{String(row.symbol ?? "")}</td>
                      <td>{String(row.action ?? "")}</td>
                      <td>{String(row.strategy_type ?? "manual")}</td>
                      <td>{String(row.strategy_role ?? "primary")}</td>
                      <td>{String(row.order_type ?? "Market")}</td>
                      <td>{String(row.status ?? "")}</td>
                      <td>{formatNumber(row.quantity, 0)}</td>
                      <td>{row.price_local ? formatLocalMoney(row.price_local, row.currency) : "n/a"}</td>
                      <td>{row.limit_price_local ? formatLocalMoney(row.limit_price_local, row.currency) : "n/a"}</td>
                      <td>{row.stop_price_local ? formatLocalMoney(row.stop_price_local, row.currency) : "n/a"}</td>
                      <td>{String(row.error_text ?? "")}</td>
                      <td>
                        {isManageable ? (
                          <div className="row-actions">
                            <button
                              className="danger-button"
                              type="button"
                              onClick={() => runAction(`/api/orders/${row.id}/manage`, { action: "cancel" })}
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <span className="muted">n/a</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="grid-2">
            <div className="stack">
              <div className="mini-grid">
                {(scheduler.data?.cycles ?? []).slice(0, 4).map((cycle) => (
                  <article className="mini-card" key={String(cycle.id)}>
                    <div className="label">Cycle #{String(cycle.id)}</div>
                    <div className="value">{String(cycle.status ?? "n/a")}</div>
                    <div className="muted">{formatTimestamp(cycle.started_at)}</div>
                  </article>
                ))}
              </div>
            </div>
            <pre className="code-block">{JSON.stringify(scheduler.data?.status ?? {}, null, 2)}</pre>
          </div>
        </section>
      ) : null}

      <LadderVisualizer
        symbol={selectedSymbol ?? ""}
        open={selectedSymbol !== null}
        onClose={() => setSelectedSymbol(null)}
      />
    </main>
  );
}
