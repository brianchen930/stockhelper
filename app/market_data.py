"""行情資料欄位正規化、有限值檢查與資料品質追蹤。"""

from __future__ import annotations

import logging
import math
from typing import Any, TypedDict

import pandas as pd

logger = logging.getLogger(__name__)

PRICE_COLUMNS = ("Open", "High", "Low", "Close")
MARKET_COLUMNS = (*PRICE_COLUMNS, "Volume")


class DataQuality(TypedDict):
    raw_count: int
    valid_count: int
    required_count: int | None
    latest_valid_date: str | None
    latest_valid_close: float | None
    previous_valid_close: float | None
    missing_fields: list[str]
    latest_missing_fields: list[str]


def is_finite_number(value: Any) -> bool:
    """只接受可轉為 float 的有限數值，拒絕 None、NaN 與正負 Infinity。"""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def normalize_history(history: pd.DataFrame | None) -> tuple[pd.DataFrame, DataQuality]:
    """將 yfinance 單層或 MultiIndex 行情整理為日期唯一且 Close 有效的資料。"""
    raw_count = 0 if history is None else len(history)
    if history is None or history.empty:
        empty = pd.DataFrame(columns=list(MARKET_COLUMNS))
        quality = _quality(
            raw_count=raw_count,
            data=empty,
            missing_fields=list(MARKET_COLUMNS),
            latest_missing_fields=list(MARKET_COLUMNS),
        )
        empty.attrs["data_quality"] = quality
        return empty, quality

    data = _flatten_market_columns(history.copy())
    absent_fields = [column for column in MARKET_COLUMNS if column not in data.columns]
    for column in absent_fields:
        data[column] = float("nan")

    invalid_fields: list[str] = []
    for column in MARKET_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        invalid = ~data[column].map(is_finite_number)
        if invalid.any():
            invalid_fields.append(column)
        data.loc[invalid, column] = float("nan")

    data = data.sort_index()
    data = data.loc[~data.index.duplicated(keep="last")]
    latest_missing_fields = [
        column
        for column in MARKET_COLUMNS
        if not is_finite_number(data.iloc[-1][column])
    ]
    # Close 是所有報酬與指標的必要欄位；Volume 缺失不應刪除價格資料。
    data = data.loc[data["Close"].map(is_finite_number)].copy()
    missing_fields = list(dict.fromkeys(absent_fields + invalid_fields))
    quality = _quality(
        raw_count=raw_count,
        data=data,
        missing_fields=missing_fields,
        latest_missing_fields=latest_missing_fields,
    )
    data.attrs["data_quality"] = quality

    logger.debug(
        "行情資料品質 raw=%s valid=%s latest_date=%s latest_close=%s previous_close=%s "
        "short_required=20 medium_required=60 missing=%s",
        quality["raw_count"],
        quality["valid_count"],
        quality["latest_valid_date"],
        quality["latest_valid_close"],
        quality["previous_valid_close"],
        quality["missing_fields"],
    )
    return data, quality


def _flatten_market_columns(data: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(data.columns, pd.MultiIndex):
        return data

    extracted: dict[str, pd.Series] = {}
    for target in MARKET_COLUMNS:
        matches = [column for column in data.columns if target in tuple(map(str, column))]
        if matches:
            extracted[target] = data[matches[0]]
    return pd.DataFrame(extracted, index=data.index)


def _quality(
    raw_count: int,
    data: pd.DataFrame,
    missing_fields: list[str],
    latest_missing_fields: list[str],
) -> DataQuality:
    valid_count = len(data)
    latest_date = None
    latest_close = None
    previous_close = None
    if valid_count:
        latest_date = _format_date(data.index[-1])
        latest_close = float(data["Close"].iloc[-1])
    if valid_count >= 2:
        previous_close = float(data["Close"].iloc[-2])
    return {
        "raw_count": raw_count,
        "valid_count": valid_count,
        "required_count": None,
        "latest_valid_date": latest_date,
        "latest_valid_close": latest_close,
        "previous_valid_close": previous_close,
        "missing_fields": missing_fields,
        "latest_missing_fields": latest_missing_fields,
    }


def _format_date(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)
