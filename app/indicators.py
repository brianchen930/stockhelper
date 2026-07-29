import pandas as pd

def calculate_moving_averages(data: pd.DataFrame):
    result = data.copy()

    result["ma5"] = result["Close"].rolling(window=5).mean()
    result["ma20"] = result["Close"].rolling(window=20).mean()
    result["ma60"] = result["Close"].rolling(window=60).mean()

    return result

def calculate_rsi(
    close_prices: pd.Series,
    period: int = 14
) -> pd.Series:
    """
    計算 RSI 指標。

    period 預設為 14。
    回傳一整條 RSI 序列。
    """

    price_change = close_prices.diff()

    gains = price_change.clip(lower=0)
    losses = -price_change.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    relative_strength = average_gain / average_loss

    rsi = 100 - (
        100 / (1 + relative_strength)
    )

    return rsi
def calculate_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    fast_ema = close.ewm(
        span=fast_period,
        adjust=False,
    ).mean()

    slow_ema = close.ewm(
        span=slow_period,
        adjust=False,
    ).mean()

    macd = fast_ema - slow_ema

    macd_signal = macd.ewm(
        span=signal_period,
        adjust=False,
    ).mean()

    macd_histogram = macd - macd_signal

    return pd.DataFrame({
        "MACD": macd,
        "MACD_SIGNAL": macd_signal,
        "MACD_HIST": macd_histogram,
    })

def calculate_kd(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 9,
) -> pd.DataFrame:
    """
    計算 KD 指標。

    period:
        RSV 計算週期，台股常用 9 日。

    回傳欄位：
        K：快線
        D：慢線
        J：敏感度更高的延伸指標
    """

    lowest_low = low.rolling(
        window=period,
        min_periods=period,
    ).min()

    highest_high = high.rolling(
        window=period,
        min_periods=period,
    ).max()

    price_range = highest_high - lowest_low

    # 避免最高價等於最低價時發生除以 0
    price_range = price_range.replace(0, pd.NA)

    rsv = (
        (close - lowest_low)
        / price_range
        * 100
    )

    k_values = []
    d_values = []

    previous_k = 50.0
    previous_d = 50.0

    for current_rsv in rsv:
        if pd.isna(current_rsv):
            k_values.append(None)
            d_values.append(None)
            continue

        current_k = (
            previous_k * 2 / 3
            + float(current_rsv) * 1 / 3
        )

        current_d = (
            previous_d * 2 / 3
            + current_k * 1 / 3
        )

        k_values.append(current_k)
        d_values.append(current_d)

        previous_k = current_k
        previous_d = current_d

    k_series = pd.Series(
        k_values,
        index=close.index,
        dtype="float64",
    )

    d_series = pd.Series(
        d_values,
        index=close.index,
        dtype="float64",
    )

    j_series = 3 * k_series - 2 * d_series

    return pd.DataFrame({
        "KD_K": k_series,
        "KD_D": d_series,
        "KD_J": j_series,
    })