from .models import Candidate


def detect_vwap(df, anchors, config):
    if df.empty:
        return []
    starts = [('rolling_vwap', max(0, len(df)-config.vwap_window))]
    for method in ('swing_low', 'swing_high'):
        eligible = [p for p in anchors if p.method == method and p.confirmed_position < len(df)]
        if eligible:
            starts.append(('anchored_vwap_' + method, max(eligible, key=lambda p: p.position).position))
    result = []
    for method, start in starts:
        bars = df.iloc[start:]
        volume = bars.Volume.sum()
        if volume > 0:
            # Daily typical-price VWAP, not intraday VWAP. Anchors are confirmed
            # by the current cutoff; never backdate availability to the pivot.
            price = (((bars.High+bars.Low+bars.Close)/3)*bars.Volume).sum()/volume
            result.append(Candidate(float(price), method, 'vwap', len(df)-1, len(df)-1))
    return result
