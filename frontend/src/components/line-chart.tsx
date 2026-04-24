"use client";

interface Point {
  x: number;
  y: number;
}

interface LineChartProps {
  values: number[];
  positive: boolean;
}

function buildPath(points: Point[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}

export function LineChart({ values, positive }: LineChartProps) {
  if (values.length === 0) {
    return <div className="chart muted">No portfolio history has been recorded yet.</div>;
  }

  const width = 920;
  const height = 260;
  const padding = 18;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = Math.max(max - min, 1);
  const points = values.map((value, index) => ({
    x: padding + (index / Math.max(values.length - 1, 1)) * (width - padding * 2),
    y: height - padding - ((value - min) / spread) * (height - padding * 2),
  }));
  const path = buildPath(points);
  const areaPath = `${path} L ${points[points.length - 1]?.x ?? width - padding} ${height - padding} L ${points[0]?.x ?? padding} ${height - padding} Z`;
  const stroke = positive ? "#0f8a4b" : "#b42318";

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio value chart">
        <defs>
          <linearGradient id="portfolio-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#portfolio-fill)" />
        <path d={path} fill="none" stroke={stroke} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
    </div>
  );
}
