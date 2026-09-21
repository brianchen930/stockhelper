import sqlite3
import json
from pathlib import Path
from app.position_status import PositionStatus, POSITION_FIELDS, position_metadata, validate_position_fields


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "data" / "stocks.db"


def get_connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def create_tables():
    connection = get_connection()
    from app.institutional_flow.storage import create_tables as create_flow_tables
    create_flow_tables(connection)
    from app.decision_state import create_table
    create_table(connection)
    from app.support_resistance_analysis.lifecycle_storage import create_tables as create_lifecycle_tables
    create_lifecycle_tables(connection)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL UNIQUE,
            stock_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    existing_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(watchlist)"
        ).fetchall()
    }

    if "position_status" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN position_status TEXT NOT NULL "
            f"DEFAULT '{PositionStatus.WATCHING.value}' "
            f"CHECK (position_status IN ('{PositionStatus.WATCHING.value}', '{PositionStatus.HOLDING.value}'))"
        )

    for name, sql_type in (('average_cost', 'REAL'), ('shares', 'INTEGER'), ('entry_date', 'TEXT')):
        if name not in existing_columns:
            connection.execute(f'ALTER TABLE watchlist ADD COLUMN {name} {sql_type}')

    if "last_signal" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_signal TEXT"
        )

    if "support_resistance_state" not in existing_columns:
        connection.execute("ALTER TABLE watchlist ADD COLUMN support_resistance_state TEXT")

    if "last_trend" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_trend TEXT"
        )

    if "last_notify_at" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_notify_at TIMESTAMP"
        )

    if "last_notification_signature" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_notification_signature TEXT"
        )

    if "last_data_error_at" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_data_error_at TIMESTAMP"
        )

    if "last_data_issue_key" not in existing_columns:
        connection.execute(
            "ALTER TABLE watchlist ADD COLUMN last_data_issue_key TEXT"
        )

    connection.commit()
    connection.close()

def add_stock(stock_code: str, stock_name: str | None = None,
              position_status: PositionStatus | str = PositionStatus.WATCHING,
              *, average_cost=None, shares=None, entry_date=None):
    position_status = PositionStatus(position_status).value
    metadata = position_metadata(validate_position_fields(dict(
        position_status=position_status, average_cost=average_cost, shares=shares, entry_date=entry_date)))
    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            INSERT INTO watchlist (stock_code, stock_name, position_status, average_cost, shares, entry_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (stock_code, stock_name, position_status, *(metadata[key] for key in POSITION_FIELDS))
        )

        connection.commit()

        return {
            "id": cursor.lastrowid,
            "stock_code": stock_code,
            "stock_name": stock_name,
            **metadata,
        }

    except sqlite3.IntegrityError:
        return None

    finally:
        connection.close()


def get_all_stocks():
    connection = get_connection()

    rows = connection.execute(
        """
        SELECT
            id,
            stock_code,
            stock_name,
            position_status,
            average_cost,
            shares,
            entry_date,
            last_signal,
            last_trend,
            last_notify_at,
            last_notification_signature,
            last_data_error_at,
            last_data_issue_key,
            support_resistance_state,
            created_at
        FROM watchlist
        ORDER BY id ASC
        """
    ).fetchall()

    connection.close()

    return [dict(row, **position_metadata(dict(row))) for row in rows]


def get_stock(stock_code: str):
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT * FROM watchlist WHERE stock_code = ?", (stock_code,)
        ).fetchone()
        return dict(row, **position_metadata(dict(row))) if row else None
    finally:
        connection.close()


def update_position_status(stock_code: str, position_status: PositionStatus | str):
    return update_position(stock_code, position_status=position_status)


def update_position(stock_code: str, **changes):
    if not changes or set(changes) - {'position_status', *POSITION_FIELDS}:
        raise ValueError('請提供有效的持倉更新欄位')
    validate_position_fields(changes)
    if 'position_status' in changes:
        changes['position_status'] = PositionStatus(changes['position_status']).value
    connection = get_connection()
    try:
        # Merge and clear atomically; omitted PATCH fields retain their values.
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute(
            'SELECT * FROM watchlist WHERE stock_code = ?', (stock_code,)).fetchone()
        if row is None:
            return None
        metadata = position_metadata(dict(row) | changes)
        connection.execute(
            'UPDATE watchlist SET position_status = ?, average_cost = ?, shares = ?, entry_date = ? WHERE stock_code = ?',
            (metadata['position_status'], *(metadata[key] for key in POSITION_FIELDS), stock_code))
        connection.commit()
        return dict(row) | metadata
    finally:
        connection.close()


def update_stock_state(
    stock_code: str,
    signal: str,
    trend: str,
    notified: bool = False,
    notification_signature: str | None = None,
):
    connection = get_connection()

    if notified:
        if notification_signature is not None:
            connection.execute(
                """
                UPDATE watchlist
                SET
                    last_signal = ?,
                    last_trend = ?,
                    last_notify_at = CURRENT_TIMESTAMP,
                    last_notification_signature = ?
                WHERE stock_code = ?
                """,
                (signal, trend, notification_signature, stock_code)
            )
        else:
            connection.execute(
                """
                UPDATE watchlist
                SET
                    last_signal = ?,
                    last_trend = ?,
                    last_notify_at = CURRENT_TIMESTAMP
                WHERE stock_code = ?
                """,
                (signal, trend, stock_code)
            )
    else:
        if notification_signature is not None:
            connection.execute(
                """
                UPDATE watchlist
                SET
                    last_signal = ?,
                    last_trend = ?,
                    last_notification_signature = ?
                WHERE stock_code = ?
                """,
                (signal, trend, notification_signature, stock_code)
            )
        else:
            connection.execute(
                """
                UPDATE watchlist
                SET
                    last_signal = ?,
                    last_trend = ?
                WHERE stock_code = ?
                """,
                (signal, trend, stock_code)
            )

    connection.commit()
    connection.close()


def save_data_quality_issue(stock_code: str, issue_key: str) -> None:
    """只記錄資料問題，不覆蓋上一筆有效市場訊號與趨勢。"""
    connection = get_connection()
    connection.execute(
        """
        UPDATE watchlist
        SET last_data_error_at = CURRENT_TIMESTAMP,
            last_data_issue_key = ?
        WHERE stock_code = ?
        """,
        (issue_key, stock_code),
    )
    connection.commit()
    connection.close()


def save_support_resistance_state(stock_code: str, state: dict) -> None:
    """Persist zone observations and delivery receipts in the existing watchlist."""
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE watchlist SET support_resistance_state = ? WHERE stock_code = ?",
            (json.dumps(state, ensure_ascii=False, allow_nan=False), stock_code),
        )
        connection.commit()
    finally:
        connection.close()

def delete_stock(stock_code: str):
    connection = get_connection()

    cursor = connection.execute(
        """
        DELETE FROM watchlist
        WHERE stock_code = ?
        """,
        (stock_code,)
    )

    connection.commit()

    deleted = cursor.rowcount > 0

    connection.close()

    return deleted
