"""Reusable causal volume baselines and explicitly separated post-event research."""
import numpy as np
import pandas as pd

from .config import TouchVolumeConfig
from .support_event import finite, valid_bar


def calculate_volume_ratio(volume, baseline):
    volume, baseline = finite(volume), finite(baseline)
    if volume is None or volume < 0 or baseline is None or baseline <= 0:
        return None
    return finite(volume / baseline)


def classify_volume_level(ratio, config=None):
    config = config or TouchVolumeConfig()
    ratio = finite(ratio)
    if ratio is None or ratio < 0:
        return None
    for ceiling, label in ((config.volume_ratio_low, 'low'),
                           (config.volume_ratio_high, 'normal'),
                           (config.volume_ratio_spike, 'elevated')):
        if ratio < ceiling:
            return label
    return 'spike'


def precompute_volume(data, config=None):
    """One pass. Every baseline excludes the current bar via shift(1).

    Require all N bars; invalid/negative volume is missing, genuine zero remains
    zero. Population standard deviation (ddof=0), no centered windows or fills.
    """
    config = config or TouchVolumeConfig()
    volume = data.get('Volume', pd.Series(np.nan, index=data.index)).map(finite).astype(float)
    volume = volume.where(volume.ge(0))
    rolling = volume.rolling(config.volume_ma_period, min_periods=config.volume_ma_period)
    mean = rolling.mean().shift(1)
    std = rolling.std(ddof=0).shift(1)
    ratio = volume / mean.where(mean.gt(0))
    zscore = (volume - mean) / std.where(std.gt(0))
    result = pd.DataFrame(dict(volume=volume, volume_ma_20=mean,
                                volume_ratio=ratio, volume_zscore=zscore,
                                pre_touch_volume_avg=volume.rolling(
                                    config.pre_volume_bars, min_periods=config.pre_volume_bars).mean().shift(1)),
                          index=data.index)
    return result.where(np.isfinite(result))


def volume_at(volume_data, index, config=None):
    config = config or TouchVolumeConfig()
    row = volume_data.iloc[index]
    ratio = finite(row.volume_ratio)
    return dict(touch_volume=finite(row.volume), touch_volume_ma20=finite(row.volume_ma_20),
                touch_volume_ratio=ratio, touch_volume_level=classify_volume_level(ratio, config),
                touch_volume_zscore=finite(row.volume_zscore),
                pre_touch_volume_avg=finite(row.pre_touch_volume_avg))


def rebound_confirmation(max_rebound_atr, future_volume_ratio, config=None):
    config = config or TouchVolumeConfig()
    rebound, ratio = finite(max_rebound_atr), finite(future_volume_ratio)
    if rebound is None:
        return None
    if rebound < config.touch_success_atr:
        return 'none'
    if ratio is None:
        return None
    if ratio >= config.rebound_volume_strong:
        return 'strong'
    if ratio < config.rebound_volume_weak:
        return 'weak'
    return 'none'  # Rebound with 1.0..1.2x volume has neither strong nor weak evidence.


def post_event_volume(data, volume_data, index, atr, label, failure_bar, config=None):
    """OUTCOME ONLY. All post averages use t+1..t+K, never today's volume.

    Failure volume comes from the first close-breakdown bar ONLY if final label
    is failure. A later breakdown after a success is not a failure observation.
    """
    config = config or TouchVolumeConfig()
    future = volume_data.volume.iloc[index + 1:index + 1 + config.volume_reaction_bars]
    post = (finite(future.mean()) if len(future) == config.volume_reaction_bars and future.notna().all() else None)
    baseline = finite(volume_data.volume_ma_20.iloc[index])
    ratio = calculate_volume_ratio(post, baseline)
    pre = finite(volume_data.pre_touch_volume_avg.iloc[index])
    reaction = data.iloc[index + 1:index + 1 + config.reaction_lookahead]
    rebound = None
    atr = finite(atr)
    if (len(reaction) == config.reaction_lookahead and atr is not None and atr > 0
            and all(valid_bar(r.Low, r.High, r.Close) for r in reaction.itertuples())):
        rebound = finite((float(reaction.High.max()) - float(data.Close.iloc[index])) / atr)
    result = dict(future_3bar_avg_volume=post, future_volume_ratio=ratio,
                  post_touch_volume_avg=post, post_pre_volume_ratio=calculate_volume_ratio(post, pre),
                  rebound_volume_confirmation=rebound_confirmation(rebound, ratio, config),
                  breakdown_volume=None, breakdown_volume_ma20=None,
                  breakdown_volume_ratio=None, breakdown_volume_level=None)
    if label == 'failure' and failure_bar is not None and index < failure_bar < len(volume_data):
        row = volume_data.iloc[failure_bar]
        result.update(breakdown_volume=finite(row.volume), breakdown_volume_ma20=finite(row.volume_ma_20),
                      breakdown_volume_ratio=finite(row.volume_ratio),
                      breakdown_volume_level=classify_volume_level(row.volume_ratio, config))
    return result


def zone_volume_evidence(profile, low, high):
    """Predictor: use the already-known t-1 daily profile, not a new download.

    Allocate bin volume by zone overlap. Density is share divided by covered
    price-range share, relative to a uniform-volume profile; not a score.
    """
    missing = dict(support_volume_share=None, support_volume_density_ratio=None)
    if not profile:
        return missing
    try:
        edges = np.asarray(profile.get('bins', []), dtype=float)
        volumes = np.asarray(profile.get('volumes', []), dtype=float)
        if (len(edges) != len(volumes) + 1 or not len(volumes)
                or not np.isfinite(edges).all() or not np.isfinite(volumes).all()
                or (volumes < 0).any() or volumes.sum() <= 0):
            return missing
        widths = np.diff(edges)
        if (widths <= 0).any():
            return missing
        overlap = np.maximum(0, np.minimum(edges[1:], high) - np.maximum(edges[:-1], low))
        share = finite(float(np.dot(volumes, overlap / widths) / volumes.sum()))
        coverage = float(overlap.sum() / (edges[-1] - edges[0]))
        return dict(support_volume_share=share,
                    support_volume_density_ratio=calculate_volume_ratio(share, coverage))
    except (TypeError, ValueError, OverflowError):
        return missing
