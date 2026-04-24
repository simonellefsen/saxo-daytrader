export type JsonObject = Record<string, any>;

export interface OverviewResponse {
  app: {
    project_name?: string;
    environment?: string;
    config_path?: string;
  };
  execution: {
    mode?: string;
    adapter?: string;
    require_approval_live: boolean;
    max_daily_orders: number;
    counts: Record<string, number>;
  };
  portfolio_summary: JsonObject;
  after_tax_summary: JsonObject;
  integrity: {
    healthy: boolean;
    warnings: string[];
    mismatches: Array<Record<string, any>>;
    unreconciled_orders: Array<Record<string, any>>;
  };
  analysis_summary: {
    analysis_window_active: boolean;
    active_markets: string[];
    active_windows: string[];
    pre_sync_markets: string[];
  };
  latest_decision: {
    id?: number | null;
    created_at?: string | null;
    status?: string | null;
  };
  scheduler_status: JsonObject | null;
  scheduler_health: JsonObject | null;
  refresh: {
    price_poll_interval_minutes: number;
    scheduler_poll_interval_minutes: number;
    decision_interval_minutes: number;
  };
}

export interface PositionsResponse {
  items: Array<Record<string, any>>;
  total: number;
}

export interface AssetLadderHistoryResponse {
  symbol: string;
  range_key: string;
  position: Record<string, any> | null;
  ladder_summary: Record<string, any>;
  chart: {
    points: Array<Record<string, any>>;
    error?: string | null;
    first_event_at?: string | null;
  };
  markers: Array<Record<string, any>>;
  active_lines: Array<Record<string, any>>;
  ladder_levels: Array<Record<string, any>>;
}

export interface PerformanceResponse {
  range_key: string;
  history: Array<Record<string, any>>;
  goal_tracking: Record<string, any>;
}

export interface MarketResponse {
  items: Array<Record<string, any>>;
  summary: Record<string, any>;
}

export interface DecisionResponse {
  report: Record<string, any> | null;
  next_report?: Record<string, any> | null;
}

export interface DecisionHistoryResponse {
  items: Array<Record<string, any>>;
}

export interface ExecutionResponse {
  orders: Array<Record<string, any>>;
  fills: Array<Record<string, any>>;
  events: Array<Record<string, any>>;
}

export interface SchedulerResponse {
  status: Record<string, any> | null;
  cycles: Array<Record<string, any>>;
}
