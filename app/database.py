import sqlite3
import json
from pathlib import Path


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

def add_stock(stock_code: str, stock_name: str | None = None):
    connection = get_connection()

    try:
        cursor = connection.execute(
            """
            INSERT INTO watchlist (stock_code, stock_name)
            VALUES (?, ?)
            """,
            (stock_code, stock_name)
        )

        connection.commit()

        return {
            "id": cursor.lastrowid,
            "stock_code": stock_code,
            "stock_name": stock_name
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

    return [dict(row) for row in rows]

    return deleted

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
