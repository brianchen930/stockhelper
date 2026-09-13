import yfinance as yf
import pandas as pd
from functools import lru_cache

from app.analysis_engine import build_technical_summary
from app.analysis import analyze_timeframes
from app.market_data import is_finite_number, normalize_history
from app.strategies import analyze_ma_strategy
from app.indicators import (
    calculate_moving_averages,
    calculate_rsi,
    calculate_macd,
    calculate_kd,
)
from app.macd_analysis import analyze_macd
from app.volatility import summarize_volatility
from app.bayesian_support.integration import attach_bayesian_support
from app.support_resistance_analysis import (
    SupportResistanceEngine,
    format_support_resistance_output,
)


def _attach_support_resistance(result, data):
    """Optional analysis failure must not discard the stock's other indicators."""
    try:
        sr = SupportResistanceEngine().detect(data)
        text = format_support_resistance_output(sr)
    except Exception:
        sr = {'error': 'Support/resistance unavailable', 'support_zones': [],
              'resistance_zones': [], 'active_zones': []}
        text = format_support_resistance_output(sr)
    result['support_resistance'] = sr
    result['support_resistance_text'] = text
    attach_bayesian_support(result, data)


@lru_cache(maxsize=500)
def resolve_yahoo_symbol(stock_code: str) -> str:
    """將原始台股代號解析為 Yahoo Finance 可用的上市或上櫃代號。"""
    code = str(stock_code).strip()
    if not code:
        raise ValueError("股票代號不可為空")

    for suffix in ("TW", "TWO"):
        symbol = f"{code}.{suffix}"
        try:
            history = yf.Ticker(symbol).history(period="5d")
        except Exception:
            continue
        if history is not None and not history.empty:
            return symbol

    raise ValueError(
        f"股票代號 {code} 在 Yahoo Finance 的 {code}.TW 與 {code}.TWO 均無有效歷史資料"
    )


def _valid_number(value) -> bool:
    return is_finite_number(value) and float(value) > 0


def _finite_or_none(value, digits: int | None = None):
    if not is_finite_number(value):
        return None
    number = float(value)
    return round(number, digits) if digits is not None else number


def _build_insufficient_analysis(
    stock_code: str,
    data: pd.DataFrame,
    data_quality: dict,
) -> dict:
    """行情少於兩筆有效 Close 時，回傳可安全顯示的分析結構。"""
    timeframe_analysis = analyze_timeframes(data)
    result = {
        "stock_code": stock_code,
        "date": data_quality.get("latest_valid_date"),
        "close": data_quality.get("latest_valid_close"),
        "change": None,
        "change_percent": None,
        "rsi": None,
        "ma5": None,
        "ma10": None,
        "ma20": None,
        "ma60": None,
        "macd": None,
        "macd_signal": None,
        "macd_histogram": None,
        "previous_macd": None,
        "previous_macd_signal": None,
        "previous_macd_histogram": None,
        "kd_k": None,
        "kd_d": None,
        "kd_j": None,
        "previous_kd_k": None,
        "previous_kd_d": None,
        "previous_kd_j": None,
        "analysis": {
            "trend": "資料不足",
            "signal": "資料不足",
            "reasons": ["最新行情資料不完整，本次不進行完整技術判斷"],
        },
        "realtime_price": None,
        "price_change": None,
        "price_change_percent": None,
        "volume": None,
        "price_source": "close",
        "benchmark_change_percent": None,
        "market_relative_performance": {
            "difference": None,
            "label": "資料不足",
            "stock_change_percent": None,
            "benchmark_change_percent": None,
        },
        "data_quality": data_quality,
        "timeframe_analysis": timeframe_analysis,
    }
    result.update(summarize_volatility(data))
    result["technical_summary"] = build_technical_summary(result)
    _attach_support_resistance(result, data)
    return result


def classify_market_relative_performance(
    stock_change_percent: float | None,
    benchmark_change_percent: float | None,
) -> dict[str, float | str | None]:
    """將個股相對大盤的表現分類為優於、略優於、持平、略弱於或弱於大盤。"""

    if not is_finite_number(stock_change_percent) or not is_finite_number(benchmark_change_percent):
        return {
            "difference": None,
            "label": "資料不足",
            "stock_change_percent": _finite_or_none(stock_change_percent),
            "benchmark_change_percent": _finite_or_none(benchmark_change_percent),
        }

    difference = round(float(stock_change_percent) - float(benchmark_change_percent), 2)
    if not is_finite_number(difference):
        return {
            "difference": None,
            "label": "資料不足",
            "stock_change_percent": _finite_or_none(stock_change_percent),
            "benchmark_change_percent": _finite_or_none(benchmark_change_percent),
        }

    if difference >= 2:
        label = "優於大盤"
    elif difference >= 0.5:
        label = "略優於大盤"
    elif difference <= -2:
        label = "弱於大盤"
    elif difference <= -0.5:
        label = "略弱於大盤"
    else:
        label = "持平大盤"

    return {
        "difference": difference,
        "label": label,
        "stock_change_percent": stock_change_percent,
        "benchmark_change_percent": benchmark_change_percent,
    }


def get_market_change_percent(index_symbol: str = "^TWII") -> float | None:
    """取得大盤指數最近一個交易日的漲跌幅。"""

    try:
        ticker = yf.Ticker(index_symbol)
        raw_data = ticker.history(
            period="5d",
            interval="1d",
            auto_adjust=False,
        )
    except Exception:
        return None

    data, _ = normalize_history(raw_data)
    if len(data) < 2:
        return None

    latest = data.iloc[-1]
    previous = data.iloc[-2]
    previous_close = float(previous["Close"])

    if previous_close == 0:
        return None

    change = float(latest["Close"]) - previous_close
    return round((change / previous_close) * 100, 2)


def get_realtime_price(stock_code: str) -> dict | None:
    """取得盤中價格；fast_info 不可用時改用最近交易日收盤資料。"""

    try:
        ticker = yf.Ticker(resolve_yahoo_symbol(stock_code))
    except ValueError:
        raise
    except Exception:
        return None
    last_price = None
    previous_close = None
    volume = None
    price_source = "realtime"
    realtime_date = None

    try:
        fast_info = ticker.fast_info
        last_price = fast_info.get("last_price")
        previous_close = fast_info.get("previous_close")
        volume = fast_info.get("last_volume", fast_info.get("volume"))
    except Exception:
        pass

    intraday = None
    daily = None

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

    if daily is not None:
        daily, _ = normalize_history(daily)
        if not daily.empty:
            realtime_date = daily.index[-1]

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
            if realtime_date is None:
                realtime_date = intraday.index[-1]
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
        "date": None if realtime_date is None else pd.Timestamp(realtime_date).strftime("%Y-%m-%d"),
        "price_change": price_change,
        "price_change_percent": price_change_percent,
        "volume": int(volume) if _valid_number(volume) else None,
        "price_source": price_source,
    }


def get_stock_price(stock_code: str):
    ticker = yf.Ticker(resolve_yahoo_symbol(stock_code))

    raw_data = ticker.history(period="3mo")
    data, _ = normalize_history(raw_data)

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
    ticker = yf.Ticker(resolve_yahoo_symbol(stock_code))

    raw_data = ticker.history(
        period=period,
        interval=interval,
        auto_adjust=False,
    )

    data, quality = normalize_history(raw_data)
    if data.empty:
        return None

    history = []

    for date, row in data.iterrows():
        history.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": _finite_or_none(row["Open"], 2),
            "high": _finite_or_none(row["High"], 2),
            "low": _finite_or_none(row["Low"], 2),
            "close": _finite_or_none(row["Close"], 2),
            "volume": int(row["Volume"]) if is_finite_number(row["Volume"]) else None,
        })

    return {
        "stock_code": stock_code,
        "period": period,
        "interval": interval,
        "count": len(history),
        "data_quality": quality,
        "history": history,
    }


def get_stock_indicators(
    stock_code: str,
    period: str = "6mo",
):
    ticker = yf.Ticker(resolve_yahoo_symbol(stock_code))

    raw_data = ticker.history(
        period=period,
        interval="1d",
        auto_adjust=False,
    )

    data, quality = normalize_history(raw_data)
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

    if not is_finite_number(latest_rsi):
        rsi_value = None
    else:
        rsi_value = round(float(latest_rsi), 2)

    return {
        "stock_code": stock_code,
        "date": data.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(latest["Close"]), 2),
        "rsi": rsi_value,
        **summarize_volatility(data),
        "ma5": (
            round(float(latest["ma5"]), 2)
            if is_finite_number(latest["ma5"])
            else None
        ),
        "ma10": (
            round(float(latest["ma10"]), 2)
            if is_finite_number(latest["ma10"])
            else None
        ),
        "ma20": (
            round(float(latest["ma20"]), 2)
            if is_finite_number(latest["ma20"])
            else None
        ),
        "ma60": (
            round(float(latest["ma60"]), 2)
            if is_finite_number(latest["ma60"])
            else None
        ),
        "data_quality": quality,
    }


def get_stock_analysis(
    stock_code: str,
    period: str = "6mo",
    *, research_mode: bool = False,
):
    ticker = yf.Ticker(resolve_yahoo_symbol(stock_code))

    raw_data = ticker.history(
        period=period,
        interval="1d",
        auto_adjust=False,
    )

    data, data_quality = normalize_history(raw_data)
    if len(data) < 2:
        return _build_insufficient_analysis(stock_code, data, data_quality)

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

    if not is_finite_number(latest_rsi):
        rsi_value = None
    else:
        rsi_value = round(float(latest_rsi), 2)

    close_price = round(float(latest["Close"]), 2)
    previous_close_price = round(float(previous["Close"]), 2)
    change = close_price - previous_close_price
    change_percent = (
        round((change / previous_close_price) * 100, 2)
        if is_finite_number(change)
        and is_finite_number(previous_close_price)
        and previous_close_price != 0
        else None
    )
    benchmark_change_percent = get_market_change_percent()
    market_relative_performance = classify_market_relative_performance(
        stock_change_percent=change_percent,
        benchmark_change_percent=benchmark_change_percent,
    )

    analysis_result = {
        "stock_code": stock_code,
        "date": data.index[-1].strftime("%Y-%m-%d"),
        "history_date": data.index[-1].strftime("%Y-%m-%d"),
        "close": close_price,
        "change": round(change, 2),
        "change_percent": change_percent,
        "rsi": rsi_value,
        "ma5": (
            round(float(latest["ma5"]), 2)
            if is_finite_number(latest["ma5"])
            else None
        ),
        "ma10": (
            round(float(latest["ma10"]), 2)
            if is_finite_number(latest["ma10"])
            else None
        ),
        "ma20": (
            round(float(latest["ma20"]), 2)
            if is_finite_number(latest["ma20"])
            else None
        ),
        "ma60": (
            round(float(latest["ma60"]), 2)
            if is_finite_number(latest["ma60"])
            else None
        ),
        "analysis": strategy_result,
        "macd": (
            round(float(latest["MACD"]), 4)
            if is_finite_number(latest["MACD"])
            else None
        ),
        "macd_signal": (
            round(float(latest["MACD_SIGNAL"]), 4)
            if is_finite_number(latest["MACD_SIGNAL"])
            else None
        ),
        "macd_histogram": (
            round(float(latest["MACD_HIST"]), 4)
            if is_finite_number(latest["MACD_HIST"])
            else None
        ),
        "previous_macd": (
            round(float(previous["MACD"]), 4)
            if is_finite_number(previous["MACD"])
            else None
        ),
        "previous_macd_signal": (
            round(float(previous["MACD_SIGNAL"]), 4)
            if is_finite_number(previous["MACD_SIGNAL"])
            else None
        ),
        "previous_macd_histogram": (
            round(float(previous["MACD_HIST"]), 4)
            if is_finite_number(previous["MACD_HIST"])
            else None
        ),
        "kd_k": (
            round(float(latest_k), 2)
            if is_finite_number(latest_k)
            else None
        ),
        "kd_d": (
            round(float(latest_d), 2)
            if is_finite_number(latest_d)
            else None
        ),
        "kd_j": (
            round(float(latest_j), 2)
            if is_finite_number(latest_j)
            else None
        ),
        "previous_kd_k": (
            round(float(previous_k), 2)
            if is_finite_number(previous_k)
            else None
        ),
        "previous_kd_d": (
            round(float(previous_d), 2)
            if is_finite_number(previous_d)
            else None
        ),
        "previous_kd_j": (
            round(float(previous_j), 2)
            if is_finite_number(previous_j)
            else None
        ),
    }

    realtime_data = get_realtime_price(stock_code) or {}
    analysis_result.update({
        "realtime_price": realtime_data.get("realtime_price", close_price),
        "realtime_date": realtime_data.get("date"),
        "price_change": realtime_data.get("price_change", round(change, 2)),
        "price_change_percent": realtime_data.get(
            "price_change_percent",
            change_percent,
        ),
        "volume": realtime_data.get("volume", int(latest["Volume"])),
        "price_source": realtime_data.get("price_source", "close"),
        "benchmark_change_percent": benchmark_change_percent,
        "market_relative_performance": market_relative_performance,
        "data_quality": data_quality,
    })
    analysis_result["macd_analysis"] = analyze_macd(
        analysis_result.get("macd"),
        analysis_result.get("macd_signal"),
        analysis_result.get("macd_histogram"),
        analysis_result.get("previous_macd_histogram"),
        analysis_result.get("previous_macd"),
        analysis_result.get("previous_macd_signal"),
    )
    analysis_result.update(summarize_volatility(data))
    analysis_result["technical_summary"] = build_technical_summary(analysis_result)
    # Display-only daily-close analysis; never feeds signal or RuleEngine scores.
    _attach_support_resistance(analysis_result, data)
    if research_mode:
        analysis_result['support_resistance_text'] = format_support_resistance_output(
            analysis_result['support_resistance'], research_mode=True)
    analysis_result["timeframe_analysis"] = analyze_timeframes(data, analysis_result['support_resistance'])

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
