"use client";

import { useMemo, useState } from "react";

import useSWR from "swr";

import { getFetcher } from "@/lib/api";
import { formatDkk, formatLocalMoney, formatNumber, formatTimestamp } from "@/lib/format";
import type { AssetLadderHistoryResponse } from "@/lib/types";

const RANGE_OPTIONS = ["1H", "4H", "SESSION"] as const;

interface LadderVisualizerProps {
  symbol: string;
  open: boolean;
  onClose: () => void;
}

function withinRange(isoTime: string | null | undefined, startAtMs: number) {
  if (!isoTime) return false;
  const parsed = new Date(isoTime).getTime();
  return Number.isFinite(parsed) && parsed >= startAtMs;
}

export function LadderVisualizer({ symbol, open, onClose }: LadderVisualizerProps) {
  const [rangeKey, setRangeKey] = useState<(typeof RANGE_OPTIONS)[number]>("SESSION");
  const [showFills, setShowFills] = useState(true);
  const [showRungs, setShowRungs] = useState(true);
  const [showAmendments, setShowAmendments] = useState(true);
  const [selectedMarkerId, setSelectedMarkerId] = useState<string | null>(null);

  const history = useSWR<AssetLadderHistoryResponse>(
    open ? `/api/asset-ladder-history/${encodeURIComponent(symbol)}?range_key=${rangeKey}` : null,
    getFetcher,
    { refreshInterval: 30_000 },
  );

  const chartPoints = history.data?.chart?.points ?? [];
  const chartError = history.data?.chart?.error;
  const markers = history.data?.markers ?? [];
  const activeLines = history.data?.active_lines ?? [];
  const ladderLevels = history.data?.ladder_levels ?? [];
  const position = history.data?.position ?? null;
  const ladderSummary = history.data?.ladder_summary ?? {};

  const filteredMarkers = useMemo(() => {
    return markers.filter((marker) => {
      const kind = String(marker.kind ?? "");
      if (kind.includes("fill")) return showFills;
      if (kind === "amendment") return showAmendments;
      return true;
    });
  }, [markers, showAmendments, showFills]);

  const selectedMarker = filteredMarkers.find((marker) => String(marker.id) === selectedMarkerId) ?? filteredMarkers[0] ?? null;

  const chartModel = useMemo(() => {
    if (!chartPoints.length) {
      return null;
    }
    const width = 920;
    const height = 360;
    const top = 20;
    const bottom = 24;
    const left = 18;
    const right = 18;
    const times = chartPoints.map((point) => new Date(String(point.time)).getTime()).filter(Number.isFinite);
    if (!times.length) {
      return null;
    }
    const lows = chartPoints.map((point) => Number(point.low ?? point.close ?? 0));
    const highs = chartPoints.map((point) => Number(point.high ?? point.close ?? 0));
    const overlayPrices = [
      ...activeLines.map((row) => Number(row.price ?? 0)),
      ...ladderLevels.map((row) => Number(row.price ?? 0)),
      ...filteredMarkers.map((row) => Number(row.price ?? 0)),
    ].filter((value) => Number.isFinite(value) && value > 0);
    const minPrice = Math.min(...lows, ...(overlayPrices.length ? overlayPrices : [Math.min(...lows)]));
    const maxPrice = Math.max(...highs, ...(overlayPrices.length ? overlayPrices : [Math.max(...highs)]));
    const priceSpread = Math.max(maxPrice - minPrice, 0.01);
    const xRange = Math.max(times[times.length - 1] - times[0], 1);
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const candleWidth = Math.max(3, Math.min(10, plotWidth / Math.max(chartPoints.length, 1) * 0.7));

    const xFor = (timeValue: number) => left + ((timeValue - times[0]) / xRange) * plotWidth;
    const yFor = (priceValue: number) => top + (1 - (priceValue - minPrice) / priceSpread) * plotHeight;

    return {
      width,
      height,
      top,
      bottom,
      left,
      right,
      candleWidth,
      xFor,
      yFor,
      minPrice,
      maxPrice,
      points: chartPoints.map((point) => {
        const timeValue = new Date(String(point.time)).getTime();
        return {
          ...point,
          open: Number(point.open ?? point.close ?? 0),
          close: Number(point.close ?? point.open ?? 0),
          time: String(point.time ?? ""),
          timeValue,
          x: xFor(timeValue),
          yOpen: yFor(Number(point.open ?? point.close ?? 0)),
          yClose: yFor(Number(point.close ?? point.open ?? 0)),
          yHigh: yFor(Number(point.high ?? point.close ?? 0)),
          yLow: yFor(Number(point.low ?? point.close ?? 0)),
        };
      }),
    };
  }, [activeLines, chartPoints, filteredMarkers, ladderLevels]);

  if (!open) {
    return null;
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={`Ladder Visualizer ${symbol}`}>
      <div className="overlay-backdrop" onClick={onClose} />
      <div className="drawer">
        <header className="drawer-header">
          <div>
            <h2>Ladder Visualizer · {symbol}</h2>
            <p>
              {position ? `${String(position.instrument_name ?? symbol)} · ${String(ladderSummary.text ?? "idle")}` : "Waiting for first fill"}
            </p>
          </div>
          <button className="ghost-button" type="button" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="mini-grid">
          <article className="mini-card">
            <div className="label">Paid Price</div>
            <div className="value">
              {position ? formatLocalMoney(position.paid_price_local, position.currency) : "n/a"}
            </div>
          </article>
          <article className="mini-card">
            <div className="label">Current Price</div>
            <div className="value">
              {position ? formatLocalMoney(position.current_price_local, position.currency) : "n/a"}
            </div>
          </article>
          <article className="mini-card">
            <div className="label">Unrealised P/L</div>
            <div className={`value ${Number(position?.unrealised_pnl_dkk ?? 0) >= 0 ? "positive" : "negative"}`}>
              {position ? formatDkk(position.unrealised_pnl_dkk) : "n/a"}
            </div>
          </article>
          <article className="mini-card">
            <div className="label">Cash Impact</div>
            <div className="value">{position ? formatDkk(position.market_value_dkk) : "n/a"}</div>
            <div className="subvalue">Allocation {position ? formatNumber(Number(position.allocation_pct ?? 0) * 100, 2) : "0"}%</div>
          </article>
        </div>

        <div className="action-row ladder-controls">
          <div className="range-picker">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option}
                className={`range-button ${rangeKey === option ? "active" : ""}`}
                type="button"
                onClick={() => setRangeKey(option)}
              >
                {option}
              </button>
            ))}
          </div>
          <label className="toggle"><input checked={showFills} onChange={(e) => setShowFills(e.target.checked)} type="checkbox" /> Show only fills</label>
          <label className="toggle"><input checked={showRungs} onChange={(e) => setShowRungs(e.target.checked)} type="checkbox" /> Show ladder rungs</label>
          <label className="toggle"><input checked={showAmendments} onChange={(e) => setShowAmendments(e.target.checked)} type="checkbox" /> Show amendments</label>
        </div>

        <div className="grid-2 ladder-grid">
          <div className="chart-panel">
            {chartError ? <div className="banner warn">{chartError}</div> : null}
            {!chartModel ? (
              <div className="chart muted">Waiting for first fill or chart data.</div>
            ) : (
              <div className="chart ladder-chart">
                <svg viewBox={`0 0 ${chartModel.width} ${chartModel.height}`} role="img" aria-label={`${symbol} ladder chart`}>
                  {showRungs
                    ? ladderLevels.map((line, index) => (
                        <g key={`${String(line.kind)}-${index}`}>
                          <line
                            x1={chartModel.left}
                            x2={chartModel.width - chartModel.right}
                            y1={chartModel.yFor(Number(line.price))}
                            y2={chartModel.yFor(Number(line.price))}
                            stroke={String(line.color ?? "#9ca3af")}
                            strokeDasharray="3 4"
                            strokeOpacity="0.5"
                          />
                        </g>
                      ))
                    : null}
                  {activeLines.map((line, index) => (
                    <g key={`active-${index}`}>
                      <line
                        x1={chartModel.left}
                        x2={chartModel.width - chartModel.right}
                        y1={chartModel.yFor(Number(line.price))}
                        y2={chartModel.yFor(Number(line.price))}
                        stroke={String(line.color ?? "#111827")}
                        strokeDasharray={line.dashed ? "6 4" : undefined}
                        strokeWidth="2"
                      />
                    </g>
                  ))}
                  {chartModel.points.map((point, index) => {
                    const isUp = Number(point.close) >= Number(point.open);
                    const fill = isUp ? "#0f8a4b" : "#b42318";
                    const candleTop = Math.min(point.yOpen, point.yClose);
                    const candleHeight = Math.max(Math.abs(point.yClose - point.yOpen), 1.5);
                    return (
                      <g key={`${String(point.time)}-${index}`}>
                        <line x1={point.x} x2={point.x} y1={point.yHigh} y2={point.yLow} stroke={fill} strokeOpacity="0.75" />
                        <rect
                          x={point.x - chartModel.candleWidth / 2}
                          y={candleTop}
                          width={chartModel.candleWidth}
                          height={candleHeight}
                          fill={fill}
                          fillOpacity="0.3"
                          stroke={fill}
                        />
                      </g>
                    );
                  })}
                  {filteredMarkers
                    .filter((marker) => withinRange(String(marker.time ?? ""), chartModel.points[0]?.timeValue ?? 0))
                    .map((marker) => {
                      const timeValue = new Date(String(marker.time)).getTime();
                      const x = chartModel.xFor(timeValue);
                      const y = chartModel.yFor(Number(marker.price ?? chartModel.minPrice));
                      const kind = String(marker.kind ?? "");
                      const color =
                        kind === "buy_fill"
                          ? "#0f8a4b"
                          : kind === "sell_fill"
                            ? "#b42318"
                            : kind === "amendment"
                              ? "#f59e0b"
                              : kind === "flatten"
                                ? "#7c3aed"
                                : "#2563eb";
                      return (
                        <g
                          key={String(marker.id)}
                          className="chart-marker"
                          onClick={() => setSelectedMarkerId(String(marker.id))}
                        >
                          <title>
                            {`${String(marker.label)} · ${formatTimestamp(marker.time)} · ${formatLocalMoney(marker.price, position?.currency)} · Qty ${formatNumber(marker.quantity, 0)}`}
                          </title>
                          <circle cx={x} cy={y} r={selectedMarkerId === String(marker.id) ? 6 : 4.5} fill={color} />
                        </g>
                      );
                    })}
                </svg>
              </div>
            )}
          </div>
          <aside className="marker-panel">
            <div className="mini-card">
              <div className="label">Selected Event</div>
              {selectedMarker ? (
                <>
                  <div className="value">{String(selectedMarker.label ?? "Event")}</div>
                  <div className="muted">
                    {formatTimestamp(selectedMarker.time)} · {formatLocalMoney(selectedMarker.price, position?.currency)} · Qty {formatNumber(selectedMarker.quantity, 0)}
                  </div>
                  <div className="muted">{String(selectedMarker.details ?? "")}</div>
                  <pre className="code-block compact">{JSON.stringify(selectedMarker.payload ?? {}, null, 2)}</pre>
                </>
              ) : (
                <div className="muted">Click any marker to inspect the order or fill payload.</div>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
