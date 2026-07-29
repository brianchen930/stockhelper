import pandas as pd


def analyze_ma_strategy(data: pd.DataFrame):
    if data.empty:
        return None

    latest = data.iloc[-1]

    close = latest["Close"]
    ma5 = latest["ma5"]
    ma20 = latest["ma20"]
    ma60 = latest["ma60"]

    if pd.isna(ma5) or pd.isna(ma20) or pd.isna(ma60):
        return {
            "trend": "資料不足",
            "signal": "無法判斷",
            "reasons": [
                "歷史資料不足以計算完整均線"
            ]
        }

    reasons = []

    if close > ma5:
        reasons.append("收盤價站上 MA5")
    else:
        reasons.append("收盤價跌破 MA5")

    if ma5 > ma20:
        reasons.append("MA5 高於 MA20")
    else:
        reasons.append("MA5 低於或等於 MA20")

    if ma20 > ma60:
        reasons.append("MA20 高於 MA60")
    else:
        reasons.append("MA20 低於或等於 MA60")

    if close > ma5 > ma20 > ma60:
        trend = "多頭排列"
        signal = "偏多"
    elif close < ma5 < ma20 < ma60:
        trend = "空頭排列"
        signal = "偏空"
    else:
        trend = "均線糾結"
        signal = "觀望"

    return {
        "trend": trend,
        "signal": signal,
        "reasons": reasons
    }