import yfinance as yf
import pandas as pd

from app.analysis_engine import build_technical_summary
from app.strategies import analyze_ma_strategy
from app.indicators import (
    calculate_moving_averages,
    calculate_rsi,
    calculate_macd,
    calculate_kd,
)


def _valid_number(value) -> bool:
    try:
        return value is not None and not pd.isna(value) and float(value) > 0
    except (TypeError, ValueError):
        return False


def get_realtime_price(stock_code: str) -> dict | None:
    """取得盤中價格；fast_info 不可用時改用最近交易日收盤資料。"""

    try:
        ticker = yf.Ticker(f"{stock_code}.TW")
    except Exception:
        return None
    last_price = None
    previous_close = None
    volume = None
    price_source = "realtime"

    try:
        fast_info = ticker.fast_info
        last_price = fast_info.get("last_price")
        previous_close = fast_info.get("previous_close")
        volume = fast_info.get("last_volume", fast_info.get("volume"))
    except Exception:
        pass

    intraday = None
    daily = None

    if not _valid_number(last_price) or not _valid_number(previous_close):
        try:
            daily = ticker.history(
                period="5d",
                interval="1d",
                auto_adjust=False,
            )
        except Exception:
            daily = None

    if not _valid_number(volume):
        try:
            intraday = ticker.history(
                period="1d",
                interval="1m",
                auto_adjust=False,
            )
        except Exception:
            intraday = None

    if not _valid_number(last_price):
        if daily is None or daily.empty:
            return None
        last_price = daily.iloc[-1]["Close"]
        price_source = "close"

    if (
        not _valid_number(previous_close)
        and daily is not None
        and len(daily) >= 2
    ):
        previous_close = daily.iloc[-2]["Close"]

    if not _valid_number(volume):
        if intraday is not None and not intraday.empty:
            try:
                volume = int(intraday["Volume"].sum())
            except Exception:
                volume = None
        elif daily is not None and not daily.empty:
            volume = daily.iloc[-1]["Volume"]

    realtime_price = round(float(last_price), 2)
    price_change = None
    price_change_percent = None
    if _valid_number(previous_close):
        previous_close = float(previous_close)
        price_change = round(realtime_price - previous_close, 2)
        price_change_percent = round((price_change / previous_close) * 100, 2)

    return {
        "realtime_price": realtime_price,
        "price_change": price_change,
        "price_change_percent": price_change_percent,
        "volume": int(volume) if _valid_number(volume) else None,
        "price_source": price_source,
    }


def get_stock_price(stock_code: str):
    ticker = yf.Ticker(f"{stock_code}.TW")

    data = ticker.history(period="3mo")

    if len(data) < 2:
        return None

    today = data.iloc[-1]
    yesterday = data.iloc[-2]

    close_price = float(today["Close"])
    yesterday_close = float(yesterday["Close"])

    change = close_price - yesterday_close
    change_percent = (change / yesterday_close) * 100

    info = ticker.info

    return {
        "stock_code": stock_code,
        "stock_name": info.get("longName", "未知"),
        "close_price": round(close_price, 2),
        "change": round(change, 2),
        "change_percent": round(change_percent, 2),
        "volume": int(today["Volume"]),
    }


def get_stock_history(
    stock_code: str,
    period: str = "3mo",
    interval: str = "1d",
):
    ticker = yf.Ticker(f"{stock_code}.TW")

    data = ticker.history(
        period=period,
        interval=interval,
        auto_adjust=False,
    )

    if data.empty:
        return None

    history = []

    for date, row in data.iterrows():
        history.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
        })

    return {
        "stock_code": stock_code,
        "period": period,
        "interval": interval,
        "count": len(history),
        "history": history,
    }


def get_stock_indicators(
    stock_code: str,
    period: str = "6mo",
):
    ticker = yf.Ticker(f"{stock_code}.TW")

    data = ticker.history(
        period=period,
        interval="1d",
        auto_adjust=False,
    )

    if data.empty:
        return None

    # 計算均線
    data = calculate_moving_averages(data)

    # 計算 RSI
    data["RSI14"] = calculate_rsi(
        data["Close"],
        period=14,
    )
    kd_data = calculate_kd(
        high=data["High"],
        low=data["Low"],
        close=data["Close"],
        period=9,
    )

    data["KD_K"] = kd_data["KD_K"]
    data["KD_D"] = kd_data["KD_D"]
    data["KD_J"] = kd_data["KD_J"]

    latest = data.iloc[-1]
    latest_rsi = latest["RSI14"]

    if pd.isna(latest_rsi):
        rsi_value = None
    else:
        rsi_value = round(float(latest_rsi), 2)

    return {
        "stock_code": stock_code,
        "date": data.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(latest["Close"]), 2),
        "rsi": rsi_value,
        "ma5": (
            round(float(latest["ma5"]), 2)
            if not pd.isna(latest["ma5"])
            else None
        ),
        "ma20": (
            round(float(latest["ma20"]), 2)
            if not pd.isna(latest["ma20"])
            else None
        ),
        "ma60": (
            round(float(latest["ma60"]), 2)
            if not pd.isna(latest["ma60"])
            else None
        ),
    }


def get_stock_analysis(
    stock_code: str,
    period: str = "6mo",
):
    ticker = yf.Ticker(f"{stock_code}.TW")

    data = ticker.history(
        period=period,
        interval="1d",
        auto_adjust=False,
    )

    if data.empty:
        return None

    # 計算均線
    data = calculate_moving_averages(data)

    # 計算 RSI
    data["RSI14"] = calculate_rsi(
        data["Close"],
        period=14,
    )
    macd_data = calculate_macd(data["Close"])

    data["MACD"] = macd_data["MACD"]
    data["MACD_SIGNAL"] = macd_data["MACD_SIGNAL"]
    data["MACD_HIST"] = macd_data["MACD_HIST"]

    # 將指標計算完成後的 data 傳給策略
    strategy_result = analyze_ma_strategy(data)
    kd_data = calculate_kd(
    high=data["High"],
    low=data["Low"],
    close=data["Close"],
    period=9,
)

    data["KD_K"] = kd_data["KD_K"]
    data["KD_D"] = kd_data["KD_D"]
    data["KD_J"] = kd_data["KD_J"]

    latest = data.iloc[-1]
    previous = data.iloc[-2]
    latest_k = latest["KD_K"]
    latest_d = latest["KD_D"]
    latest_j = latest["KD_J"]

    previous_k = previous["KD_K"]
    previous_d = previous["KD_D"]
    previous_j = previous["KD_J"]
    latest_rsi = latest["RSI14"]

    if pd.isna(latest_rsi):
        rsi_value = None
    else:
        rsi_value = round(float(latest_rsi), 2)

    close_price = round(float(latest["Close"]), 2)
    previous_close_price = round(float(previous["Close"]), 2)
    change = close_price - previous_close_price
    change_percent = round((change / previous_close_price) * 100, 2) if previous_close_price != 0 else None

    analysis_result = {
        "stock_code": stock_code,
        "date": data.index[-1].strftime("%Y-%m-%d"),
        "close": close_price,
        "change": round(change, 2),
        "change_percent": change_percent,
        "rsi": rsi_value,
        "ma5": (
            round(float(latest["ma5"]), 2)
            if not pd.isna(latest["ma5"])
            else None
        ),
        "ma20": (
            round(float(latest["ma20"]), 2)
            if not pd.isna(latest["ma20"])
            else None
        ),
        "ma60": (
            round(float(latest["ma60"]), 2)
            if not pd.isna(latest["ma60"])
            else None
        ),
        "analysis": strategy_result,
        "macd": (
            round(float(latest["MACD"]), 4)
            if not pd.isna(latest["MACD"])
            else None
        ),
        "macd_signal": (
            round(float(latest["MACD_SIGNAL"]), 4)
            if not pd.isna(latest["MACD_SIGNAL"])
            else None
        ),
        "macd_histogram": (
            round(float(latest["MACD_HIST"]), 4)
            if not pd.isna(latest["MACD_HIST"])
            else None
        ),
        "previous_macd": (
            round(float(previous["MACD"]), 4)
            if not pd.isna(previous["MACD"])
            else None
        ),
        "previous_macd_signal": (
            round(float(previous["MACD_SIGNAL"]), 4)
            if not pd.isna(previous["MACD_SIGNAL"])
            else None
        ),
        "previous_macd_histogram": (
            round(float(previous["MACD_HIST"]), 4)
            if not pd.isna(previous["MACD_HIST"])
            else None
        ),
        "kd_k": (
            round(float(latest_k), 2)
            if not pd.isna(latest_k)
            else None
        ),
        "kd_d": (
            round(float(latest_d), 2)
            if not pd.isna(latest_d)
            else None
        ),
        "kd_j": (
            round(float(latest_j), 2)
            if not pd.isna(latest_j)
            else None
        ),
        "previous_kd_k": (
            round(float(previous_k), 2)
            if not pd.isna(previous_k)
            else None
        ),
        "previous_kd_d": (
            round(float(previous_d), 2)
            if not pd.isna(previous_d)
            else None
        ),
        "previous_kd_j": (
            round(float(previous_j), 2)
            if not pd.isna(previous_j)
            else None
        ),
    }

    realtime_data = get_realtime_price(stock_code) or {}
    analysis_result.update({
        "realtime_price": realtime_data.get("realtime_price", close_price),
        "price_change": realtime_data.get("price_change", round(change, 2)),
        "price_change_percent": realtime_data.get(
            "price_change_percent",
            change_percent,
        ),
        "volume": realtime_data.get("volume", int(latest["Volume"])),
        "price_source": realtime_data.get("price_source", "close"),
    })
    analysis_result["technical_summary"] = build_technical_summary(analysis_result)

    return analysis_result


def analyze_watchlist(stocks: list[dict]):
    results = []

    for stock in stocks:
        stock_code = stock["stock_code"]
        stock_name = stock.get("stock_name")

        try:
            analysis = get_stock_analysis(stock_code)

            if analysis is None:
                results.append({
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "status": "error",
                    "error": "找不到股票資料",
                })
                continue

            results.append({
                "stock_code": stock_code,
                "stock_name": stock_name,
                "status": "success",
                "data": analysis,
            })

        except Exception as error:
            results.append({
                "stock_code": stock_code,
                "stock_name": stock_name,
                "status": "error",
                "error": str(error),
            })

    return results
