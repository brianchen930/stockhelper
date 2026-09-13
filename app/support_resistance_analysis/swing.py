import numpy as np
from .models import Candidate


def detect_swings(df, config):
    n = config.swing_window
    previous = df.Close.shift(1)
    tr = np.maximum(df.High - df.Low, np.maximum(abs(df.High - previous), abs(df.Low - previous)))
    atr = tr.rolling(config.atr_period, min_periods=1).mean().fillna(df.High - df.Low)
    result = []
    for i in range(n, len(df) - n):
        # A pivot at i is only KNOWABLE at i+n. Never emit unconfirmed pivots.
        for column, method, sign in [('High', 'swing_high', 1), ('Low', 'swing_low', -1)]:
            values = df[column].iloc[i-n:i+n+1].to_numpy()
            center = values[n]
            others = np.delete(values, n)
            threshold = max(center * config.swing_min_pct, atr.iloc[i] * config.swing_atr_factor)
            left_extreme = values[:n].min() if sign > 0 else values[:n].max()
            right_extreme = values[n+1:].min() if sign > 0 else values[n+1:].max()
            if np.all(sign * center > sign * others) and min(
                sign * (center-left_extreme), sign * (center-right_extreme),
            ) >= threshold:
                result.append(Candidate(float(center), method, 'swing', i, i+n))
    return result
