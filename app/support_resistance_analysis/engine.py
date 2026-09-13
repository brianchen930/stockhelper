import logging
import numpy as np
import pandas as pd
from app.market_data import normalize_history
from app.volatility import summarize_volatility, calculate_atr_distance, calculate_zone_width_atr
from .config import SupportResistanceConfig
from .models import SupportResistanceLevel, date_at
from .swing import detect_swings
from .volume_profile import build_profile
from .vwap import detect_vwap
from .clustering import cluster_prices

logger = logging.getLogger(__name__)


def merge_candidates(candidates, current_price, config):
    """Bound each group's full center span; prevent transitive chain merging."""
    groups = []
    for candidate in sorted(candidates, key=lambda p: p.price):
        if not groups or candidate.price-groups[-1][0].price > current_price*config.merge_tolerance_pct:
            groups.append([])
        groups[-1].append(candidate)
    return groups


def classify_zone(low, high, current_price, config):
    margin = current_price*config.neutral_tolerance_pct
    if low-margin <= current_price <= high+margin:
        return 'active'
    return 'support' if high < current_price else 'resistance'


def touch_statistics(df, low, high, config):
    """One contiguous visit = at most one test, confirmed by a later close.

    Historical reactions describe today's zone, not historical tradable signals.
    Only bars already present at the analysis cutoff can confirm a reaction.
    """
    inside = ((df.Low <= high) & (df.High >= low)).to_numpy()
    count, last, confirmation = 0, None, None
    i = 0
    while i < len(df):
        if not inside[i]:
            i += 1
            continue
        start = i
        while i+1 < len(df) and inside[i+1]:
            i += 1
        end = i
        # Only evaluate from first entry; prolonged residence is not many tests.
        previous = float(df.Close.iloc[start-1]) if start else None
        for j in range(start+1, min(len(df), start+config.reaction_bars+1)):
            close = float(df.Close.iloc[j])
            rebound = close > high*(1+config.reaction_pct)
            rejection = close < low*(1-config.reaction_pct)
            if ((previous is None or previous >= high) and rebound or
                    (previous is None or previous <= low) and rejection):
                count += 1
                last, confirmation = start, j
                break
        i = end+1
    avg = float(df.Volume.mean()) if len(df) else 0
    ratio = float(df.loc[inside, 'Volume'].mean())/avg if inside.any() and avg > 0 else 0
    return count, last, confirmation, ratio


def strength_score(families, touches, volume_ratio, age, config):
    consensus = sum(config.method_weights.get(f, 0) for f in set(families))/sum(config.method_weights.values())
    components = {
        'consensus': consensus,
        'touch': min(1, touches/config.touch_saturation),
        'volume': min(1, max(0, volume_ratio-1)/config.volume_saturation),
        'recency': config.recency_floor+(1-config.recency_floor)*2**(-age/config.recency_half_life),
    }
    score = 10*sum(config.score_weights.get(k, 0)*v for k, v in components.items())/sum(config.score_weights.values())
    return round(score, 2)


class SupportResistanceEngine:
    def __init__(self, config=None):
        self.config = config or SupportResistanceConfig()

    def detect(self, df, *, as_of=None, current_price=None):
        c = self.config
        result = dict(current_price=None, support_zones=[], resistance_zones=[], active_zones=[],
                      nearest_support=None, nearest_resistance=None, summary_key_levels=[],
                      warnings=[], candidates=[], volume_profile={}, price_basis='daily_close')
        # Slice BEFORE normalization, pivot confirmation, profile, or scoring.
        if as_of is not None and df is not None and not df.empty:
            df = df.loc[df.index <= as_of]
        data, quality = normalize_history(df)
        # Full available history at the cutoff; independent of zone lookback and
        # legacy swing thresholds. The new ATR is metadata only.
        result.update(summarize_volatility(data))
        result.update(nearest_support_distance_atr=None, nearest_resistance_distance_atr=None)
        if quality['missing_fields']:
            result['warnings'].append('Missing/invalid fields: ' + ', '.join(quality['missing_fields']))
        if data.empty:
            result['error'] = 'No valid OHLCV data'
            return result
        data = data.copy()
        data['Volume'] = data.Volume.fillna(0).clip(lower=0)
        # Missing Open does not prevent HLC-based methods. Never fabricate H/L.
        valid = (data.Low > 0) & (data.High >= data.Low) & data.Close.between(data.Low, data.High)
        valid &= data.High/data.Low <= c.max_bar_range_ratio
        # Sequential guard uses only the last accepted close (no future median).
        previous = None
        accepted = []
        for row, ok in zip(data.itertuples(), valid):
            ok = bool(ok)
            if ok and previous is not None:
                ok = max(row.Close/previous, previous/row.Close) <= c.max_close_jump_ratio
            accepted.append(ok)
            if ok:
                previous = row.Close
        if not all(accepted):
            result['warnings'].append('Invalid or extreme price bars excluded; verify corporate actions')
        data = data.loc[accepted].tail(c.lookback)
        if data.empty:
            result['error'] = 'No valid OHLC price bars'
            return result
        price = float(data.Close.iloc[-1]) if current_price is None else float(current_price)
        if not np.isfinite(price) or price <= 0:
            result['error'] = 'Invalid current price'
            return result
        result.update(current_price=price, as_of=date_at(data, len(data)-1),
                      price_basis='daily_close' if current_price is None else 'explicit_price')
        if len(data) < 2*c.swing_window+1:
            result['warnings'].append('Insufficient bars for confirmed swings')

        def safe(name, function, fallback):
            try:
                return function()
            except Exception as error:
                logger.warning('Support/resistance %s unavailable: %s', name, error)
                result['warnings'].append(name + ' unavailable')
                return fallback

        swings = safe('swing', lambda: detect_swings(data, c), [])
        profile, summary = safe('volume_profile', lambda: build_profile(data, c), ([], {}))
        vwaps = safe('vwap', lambda: detect_vwap(data, swings, c), [])
        clusters = safe('kmeans', lambda: cluster_prices(swings+profile, c), [])
        candidates = swings+profile+vwaps+clusters
        result['volume_profile'] = summary
        result['fractals'] = {key: [p.price for p in swings if p.method == method]
                              for key, method in [('supports', 'swing_low'), ('resistances', 'swing_high')]}
        result['kmeans_clusters'] = [p.price for p in clusters]
        result['dynamic_levels'] = {p.method: p.price for p in vwaps}
        result['candidates'] = [dict(price=p.price, method=p.method, family=p.family,
                                     date=date_at(data, p.position), confirmed_date=date_at(data, p.confirmed_position))
                                for p in candidates]
        zones = []
        for group in merge_candidates(candidates, price, c):
            low = min(p.low if p.low is not None else p.price-price*c.zone_padding_pct for p in group)
            high = max(p.high if p.high is not None else p.price+price*c.zone_padding_pct for p in group)
            low = max(low, np.finfo(float).tiny)
            center = (low+high)/2
            distance = (center/price-1)*100
            kind = classify_zone(low, high, price, c)
            if kind != 'active' and abs(distance) > c.max_distance_pct*100:
                continue
            touches, last, confirmed, volume = touch_statistics(data, low, high, c)
            seen = max([p.position for p in group]+([confirmed] if confirmed is not None else []))
            families = set(p.family for p in group)
            score = strength_score(families, touches, volume, len(data)-1-seen, c)
            label = max((entry for entry in c.strength_thresholds if entry[0] <= score), default=(0, 'weak'))[1]
            zones.append(SupportResistanceLevel(kind, float(low), float(high), float(center), score, label,
                sorted(set(p.method for p in group)), touches, round(distance, 4),
                date_at(data, last) if last is not None else None, date_at(data, seen), len(families)).to_dict())
        zones.sort(key=lambda z: (abs(z['distance_pct'])/100-c.ranking_strength_weight*z['strength_score']/10,
                                  -z['strength_score'], z['center']))
        for zone in zones:
            zone['zone_width_atr'] = calculate_zone_width_atr(zone['low'], zone['high'], result['atr'])
        for kind, limit in [('support', c.max_support_zones), ('resistance', c.max_resistance_zones), ('active', c.max_active_zones)]:
            result[kind+'_zones'] = [z for z in zones if z['type'] == kind][:limit]
        for kind in ('support', 'resistance'):
            result['nearest_'+kind] = min(result[kind+'_zones'], key=lambda z: abs(z['distance_pct']), default=None)
            # Nearest boundary across displayed zones; keep legacy center-based
            # nearest-zone selection intact for existing event consumers.
            distances = [calculate_atr_distance(price, z['high'] if kind == 'support' else z['low'], result['atr'])
                         for z in result[kind+'_zones']]
            result['nearest_'+kind+'_distance_atr'] = min((d for d in distances if d is not None), default=None)
        result['summary_key_levels'] = result['active_zones']+result['support_zones']+result['resistance_zones']
        return result
