from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS import_batches (
            batch_id TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            source_csv TEXT NOT NULL,
            source_position_count INTEGER NOT NULL,
            imported_position_count INTEGER NOT NULL,
            excluded_position_count INTEGER NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS position_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            instrument_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            isin TEXT,
            quantity REAL NOT NULL,
            currency TEXT NOT NULL,
            open_price_local REAL,
            current_price_local REAL,
            cost_basis_local REAL,
            cost_basis_dkk REAL NOT NULL,
            market_value_local REAL,
            market_value_dkk REAL NOT NULL,
            unrealised_pnl_dkk REAL NOT NULL,
            daily_pnl_dkk REAL,
            allocation_pct REAL,
            status TEXT,
            account_name TEXT,
            asset_class TEXT,
            market_status TEXT,
            value_date TEXT,
            source_csv TEXT NOT NULL,
            excluded INTEGER NOT NULL DEFAULT 0,
            exclusion_reason TEXT,
            raw_payload_json TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES import_batches(batch_id)
        );

        CREATE INDEX IF NOT EXISTS idx_position_snapshots_batch
        ON position_snapshots(batch_id, excluded);

        CREATE TABLE IF NOT EXISTS position_lots (
            lot_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            acquired_at TEXT,
            symbol TEXT NOT NULL,
            isin TEXT,
            figi TEXT,
            instrument_name TEXT NOT NULL,
            quantity_original REAL NOT NULL,
            currency TEXT NOT NULL,
            cost_basis_total_local REAL,
            cost_basis_total_dkk REAL NOT NULL,
            fx_rate_to_dkk REAL NOT NULL,
            source_type TEXT NOT NULL,
            source_reference TEXT,
            raw_payload_json TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES import_batches(batch_id)
        );

        CREATE INDEX IF NOT EXISTS idx_position_lots_symbol
        ON position_lots(symbol, created_at);

        CREATE TABLE IF NOT EXISTS trade_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            isin TEXT,
            figi TEXT,
            instrument_name TEXT,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            price_local REAL NOT NULL,
            currency TEXT NOT NULL,
            gross_amount_dkk REAL NOT NULL,
            commission_dkk REAL NOT NULL,
            tax_dkk REAL NOT NULL,
            net_amount_dkk REAL NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            portfolio_before_json TEXT,
            portfolio_after_json TEXT,
            decision_context_json TEXT
        );

        CREATE TABLE IF NOT EXISTS lot_realizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            ledger_id INTEGER NOT NULL,
            lot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity_sold REAL NOT NULL,
            cost_basis_allocated_local REAL,
            cost_basis_allocated_dkk REAL NOT NULL,
            proceeds_allocated_dkk REAL NOT NULL,
            realised_gain_dkk REAL NOT NULL,
            raw_payload_json TEXT NOT NULL,
            FOREIGN KEY(ledger_id) REFERENCES trade_ledger(id),
            FOREIGN KEY(lot_id) REFERENCES position_lots(lot_id)
        );

        CREATE INDEX IF NOT EXISTS idx_lot_realizations_lot
        ON lot_realizations(lot_id, ledger_id);

        CREATE TABLE IF NOT EXISTS decision_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            report_date TEXT NOT NULL,
            batch_id TEXT,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            analysis_window_active INTEGER NOT NULL DEFAULT 0,
            response_id TEXT,
            prompt_text TEXT NOT NULL,
            request_json TEXT NOT NULL,
            response_json TEXT,
            report_json TEXT,
            error_text TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_decision_reports_created
        ON decision_reports(created_at DESC);

        CREATE TABLE IF NOT EXISTS execution_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            report_id INTEGER,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            adapter TEXT NOT NULL,
            requested_weight_pct REAL,
            quantity REAL,
            price_local REAL,
            currency TEXT,
            estimated_value_dkk REAL,
            approval_required INTEGER NOT NULL DEFAULT 0,
            approved_at TEXT,
            ledger_id INTEGER,
            request_json TEXT NOT NULL,
            execution_result_json TEXT,
            error_text TEXT,
            FOREIGN KEY(report_id) REFERENCES decision_reports(id),
            FOREIGN KEY(ledger_id) REFERENCES trade_ledger(id)
        );

        CREATE INDEX IF NOT EXISTS idx_execution_orders_report
        ON execution_orders(report_id, status);

        CREATE TABLE IF NOT EXISTS execution_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            execution_order_id INTEGER NOT NULL,
            broker_order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            fill_status TEXT NOT NULL,
            cumulative_quantity REAL NOT NULL,
            delta_quantity REAL NOT NULL,
            average_price_local REAL NOT NULL,
            currency TEXT NOT NULL,
            ledger_id INTEGER,
            raw_payload_json TEXT NOT NULL,
            FOREIGN KEY(execution_order_id) REFERENCES execution_orders(id),
            FOREIGN KEY(ledger_id) REFERENCES trade_ledger(id)
        );

        CREATE INDEX IF NOT EXISTS idx_execution_fills_order
        ON execution_fills(execution_order_id, cumulative_quantity, created_at);

        CREATE TABLE IF NOT EXISTS execution_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            execution_order_id INTEGER NOT NULL,
            broker_order_id TEXT,
            event_type TEXT NOT NULL,
            broker_status TEXT,
            broker_substatus TEXT,
            broker_quantity REAL,
            broker_price_local REAL,
            event_signature TEXT NOT NULL,
            raw_payload_json TEXT NOT NULL,
            FOREIGN KEY(execution_order_id) REFERENCES execution_orders(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_order_events_signature
        ON execution_order_events(event_signature);

        CREATE INDEX IF NOT EXISTS idx_execution_order_events_order
        ON execution_order_events(execution_order_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            summary_date TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            subject TEXT NOT NULL,
            message_text TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            error_text TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_notification_deliveries_summary
        ON notification_deliveries(summary_date, channel, status, created_at DESC);

        CREATE TABLE IF NOT EXISTS notification_channel_state (
            channel TEXT PRIMARY KEY,
            summary_date TEXT,
            last_attempt_at TEXT,
            next_attempt_after TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_status TEXT,
            last_error_text TEXT
        );

        CREATE TABLE IF NOT EXISTS notification_alert_state (
            scope_key TEXT PRIMARY KEY,
            severity TEXT NOT NULL,
            last_sent_at TEXT,
            last_alert_key TEXT,
            last_summary_kind TEXT,
            last_delivery_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS scheduler_status (
            singleton_key TEXT PRIMARY KEY,
            started_at TEXT,
            last_heartbeat_at TEXT,
            last_cycle_started_at TEXT,
            last_cycle_completed_at TEXT,
            last_cycle_status TEXT,
            last_cycle_json TEXT,
            scheduler_pid INTEGER
        );

        CREATE TABLE IF NOT EXISTS scheduler_cycle_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            analysis_window_active INTEGER NOT NULL DEFAULT 0,
            generated_decision INTEGER NOT NULL DEFAULT 0,
            queue_status TEXT,
            notifications_status TEXT,
            broker_alerts_status TEXT,
            cycle_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scheduler_cycle_history_started
        ON scheduler_cycle_history(started_at DESC);

        CREATE TABLE IF NOT EXISTS portfolio_price_snapshots (
            symbol TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            baseline_session_date TEXT NOT NULL,
            baseline_at TEXT,
            current_price_local REAL,
            current_fx_rate_to_dkk REAL,
            previous_close_local REAL,
            change_pct REAL,
            currency TEXT,
            source TEXT,
            status TEXT,
            baseline_price_local REAL,
            baseline_fx_rate_to_dkk REAL
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_price_snapshots_baseline
        ON portfolio_price_snapshots(baseline_session_date, updated_at DESC);

        CREATE TABLE IF NOT EXISTS portfolio_value_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            snapshot_type TEXT NOT NULL,
            baseline_session_date TEXT,
            batch_id TEXT,
            total_market_value_dkk REAL NOT NULL,
            invested_market_value_dkk REAL NOT NULL,
            cash_balance_dkk REAL NOT NULL,
            total_cost_basis_dkk REAL NOT NULL,
            total_unrealised_pnl_dkk REAL NOT NULL,
            total_daily_pnl_dkk REAL NOT NULL,
            position_count INTEGER NOT NULL,
            source TEXT,
            raw_payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_value_history_recorded
        ON portfolio_value_history(recorded_at DESC);

        CREATE TABLE IF NOT EXISTS broker_position_snapshots (
            symbol TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            instrument_name TEXT,
            isin TEXT,
            uic INTEGER,
            asset_type TEXT,
            quantity REAL NOT NULL,
            currency TEXT,
            open_price_local REAL,
            open_price_including_costs_local REAL,
            execution_time_open TEXT,
            value_date TEXT,
            market_state TEXT,
            can_be_closed INTEGER,
            raw_payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_broker_position_snapshots_updated
        ON broker_position_snapshots(updated_at DESC);

        CREATE TABLE IF NOT EXISTS broker_balance_snapshots (
            singleton_key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            currency TEXT,
            cash_available_for_trading REAL,
            margin_available_for_trading REAL,
            cash_balance REAL,
            transactions_not_booked REAL,
            settlement_value REAL,
            total_value REAL,
            raw_payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS broker_account_snapshots (
            singleton_key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            account_key TEXT,
            account_id TEXT,
            account_currency TEXT,
            is_trial_account INTEGER,
            fractional_order_enabled INTEGER,
            fractional_order_enabled_asset_types_json TEXT,
            can_use_cash_positions_as_margin_collateral INTEGER,
            use_cash_positions_as_margin_collateral INTEGER,
            legal_asset_types_json TEXT,
            raw_payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS broker_instrument_exposures (
            symbol TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            uic INTEGER,
            asset_type TEXT,
            quantity REAL,
            average_open_price REAL,
            profit_loss_on_trade REAL,
            instrument_price_day_percent_change REAL,
            currency TEXT,
            calculation_reliability TEXT,
            can_be_closed INTEGER,
            raw_payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_broker_instrument_exposures_updated
        ON broker_instrument_exposures(updated_at DESC);

        CREATE TABLE IF NOT EXISTS portfolio_reconciliation_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            instrument_name TEXT,
            isin TEXT,
            currency TEXT,
            quantity_delta REAL NOT NULL,
            cost_basis_local_delta REAL NOT NULL,
            cost_basis_dkk_delta REAL NOT NULL,
            local_quantity_before REAL NOT NULL,
            broker_quantity_target REAL NOT NULL,
            note TEXT,
            raw_payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_portfolio_reconciliation_adjustments_symbol
        ON portfolio_reconciliation_adjustments(symbol, created_at DESC);

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL
        );
        """
    )
    _ensure_column(connection, "trade_ledger", "commission_local", "REAL")
    _ensure_column(connection, "trade_ledger", "figi", "TEXT")
    _ensure_column(connection, "trade_ledger", "instrument_name", "TEXT")
    _ensure_column(connection, "trade_ledger", "fx_conversion_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "realised_gain_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "cost_basis_sold_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "cost_basis_sold_local", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "realised_gain_local", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "fx_gain_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "price_gain_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "sale_fx_rate_to_dkk", "REAL")
    _ensure_column(connection, "trade_ledger", "cost_basis_fx_rate_to_dkk", "REAL")
    _ensure_column(connection, "trade_ledger", "tax_year", "INTEGER")
    _ensure_column(connection, "trade_ledger", "batch_id", "TEXT")
    _ensure_column(connection, "position_lots", "figi", "TEXT")
    _ensure_column(connection, "execution_orders", "broker_order_id", "TEXT")
    _ensure_column(connection, "notification_deliveries", "summary_kind", "TEXT NOT NULL DEFAULT 'daily'")
    connection.commit()


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    existing_columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in existing_columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def append_audit_log(connection: sqlite3.Connection, event_type: str, payload: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO audit_log (created_at, event_type, event_json)
        VALUES (CURRENT_TIMESTAMP, ?, ?)
        """,
        (event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    connection.commit()


def update_scheduler_status(
    connection: sqlite3.Connection,
    *,
    started_at: str | None = None,
    last_heartbeat_at: str | None = None,
    last_cycle_started_at: str | None = None,
    last_cycle_completed_at: str | None = None,
    last_cycle_status: str | None = None,
    last_cycle_json: dict[str, Any] | None = None,
    scheduler_pid: int | None = None,
) -> None:
    existing = connection.execute(
        "SELECT * FROM scheduler_status WHERE singleton_key = 'main'"
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO scheduler_status (
                singleton_key, started_at, last_heartbeat_at, last_cycle_started_at,
                last_cycle_completed_at, last_cycle_status, last_cycle_json, scheduler_pid
            ) VALUES ('main', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                last_heartbeat_at,
                last_cycle_started_at,
                last_cycle_completed_at,
                last_cycle_status,
                json.dumps(last_cycle_json, ensure_ascii=False, sort_keys=True) if last_cycle_json is not None else None,
                scheduler_pid,
            ),
        )
    else:
        row = dict(existing)
        connection.execute(
            """
            UPDATE scheduler_status
            SET started_at = ?,
                last_heartbeat_at = ?,
                last_cycle_started_at = ?,
                last_cycle_completed_at = ?,
                last_cycle_status = ?,
                last_cycle_json = ?,
                scheduler_pid = ?
            WHERE singleton_key = 'main'
            """,
            (
                started_at if started_at is not None else row["started_at"],
                last_heartbeat_at if last_heartbeat_at is not None else row["last_heartbeat_at"],
                last_cycle_started_at if last_cycle_started_at is not None else row["last_cycle_started_at"],
                last_cycle_completed_at if last_cycle_completed_at is not None else row["last_cycle_completed_at"],
                last_cycle_status if last_cycle_status is not None else row["last_cycle_status"],
                json.dumps(last_cycle_json, ensure_ascii=False, sort_keys=True) if last_cycle_json is not None else row["last_cycle_json"],
                scheduler_pid if scheduler_pid is not None else row["scheduler_pid"],
            ),
        )
    connection.commit()


def fetch_scheduler_status(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM scheduler_status WHERE singleton_key = 'main'"
    ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["last_cycle_json"] = json.loads(record["last_cycle_json"]) if record.get("last_cycle_json") else None
    return record


def record_scheduler_cycle(
    connection: sqlite3.Connection,
    *,
    started_at: str,
    completed_at: str | None,
    status: str,
    analysis_window_active: bool,
    generated_decision: bool,
    queue_status: str | None,
    notifications_status: str | None,
    broker_alerts_status: str | None,
    cycle_json: dict[str, Any],
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO scheduler_cycle_history (
            started_at, completed_at, status, analysis_window_active, generated_decision,
            queue_status, notifications_status, broker_alerts_status, cycle_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            completed_at,
            status,
            1 if analysis_window_active else 0,
            1 if generated_decision else 0,
            queue_status,
            notifications_status,
            broker_alerts_status,
            json.dumps(cycle_json, ensure_ascii=False, sort_keys=True),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def fetch_scheduler_cycles(connection: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM scheduler_cycle_history
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["cycle_json"] = json.loads(record["cycle_json"]) if record.get("cycle_json") else None
        output.append(record)
    return output


def prune_scheduler_cycles(
    connection: sqlite3.Connection,
    *,
    keep_max_rows: int | None = None,
    keep_since_started_at: str | None = None,
) -> int:
    deleted_rows = 0
    if keep_since_started_at:
        cursor = connection.execute(
            """
            DELETE FROM scheduler_cycle_history
            WHERE started_at < ?
            """,
            (keep_since_started_at,),
        )
        deleted_rows += int(cursor.rowcount or 0)
    if keep_max_rows is not None and keep_max_rows > 0:
        cursor = connection.execute(
            """
            DELETE FROM scheduler_cycle_history
            WHERE id NOT IN (
                SELECT id
                FROM scheduler_cycle_history
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (keep_max_rows,),
        )
        deleted_rows += int(cursor.rowcount or 0)
    connection.commit()
    return deleted_rows
