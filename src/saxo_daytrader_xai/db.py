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

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL
        );
        """
    )
    _ensure_column(connection, "trade_ledger", "commission_local", "REAL")
    _ensure_column(connection, "trade_ledger", "fx_conversion_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "realised_gain_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "cost_basis_sold_dkk", "REAL NOT NULL DEFAULT 0")
    _ensure_column(connection, "trade_ledger", "tax_year", "INTEGER")
    _ensure_column(connection, "trade_ledger", "batch_id", "TEXT")
    _ensure_column(connection, "execution_orders", "broker_order_id", "TEXT")
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
