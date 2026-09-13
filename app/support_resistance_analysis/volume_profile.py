import numpy as np
from .models import Candidate


def build_profile(df, config):
    bars = df.tail(config.vp_window)
    if bars.empty or bars.Volume.sum() <= 0:
        return [], {'poc': None, 'bins': [], 'volumes': []}
    low, high = float(bars.Low.min()), float(bars.High.max())
    count = config.vp_bins if config.vp_bin_size is None else max(1, int(np.ceil((high-low)/config.vp_bin_size)))
    count = min(count, config.vp_max_bins)
    if low == high:
        edges = np.array([low, high])
        volumes = np.array([float(bars.Volume.sum())])
    else:
        edges = (low + np.arange(count+1) * config.vp_bin_size
                 if config.vp_bin_size and count < config.vp_max_bins else np.linspace(low, high, count+1))
        volumes = np.zeros(count)
        for row in bars.itertuples():
            # Daily OHLCV approximation: distribute volume uniformly over each
            # candle's high-low overlap with bins; this is NOT trade-level VPVR.
            if row.High == row.Low:
                volumes[min(count-1, max(0, np.searchsorted(edges, row.Low, side='right')-1))] += row.Volume
            else:
                overlap = np.maximum(0, np.minimum(edges[1:], row.High)-np.maximum(edges[:-1], row.Low))
                volumes += row.Volume * overlap / (row.High-row.Low)
    centers = (edges[:-1]+edges[1:])/2
    poc = int(np.argmax(volumes))
    nodes = [i for i, v in enumerate(volumes) if v > 0 and v >= volumes[poc]*config.hvn_fraction
             and (i == 0 or v > volumes[i-1]) and (i == len(volumes)-1 or v >= volumes[i+1])]
    result = []
    for i in sorted(set(nodes+[poc])):
        overlaps = (df.Low <= edges[i+1]) & (df.High >= edges[i]) & (df.Volume > 0)
        position = int(np.flatnonzero(overlaps)[-1])
        result.append(Candidate(float(centers[i]), 'volume_profile_poc' if i == poc else 'volume_profile_hvn',
                                'volume_profile', position, len(df)-1, float(edges[i]), float(edges[i+1]),
                                float(volumes[i]/volumes[poc])))
    return result, {'poc': float(centers[poc]), 'bins': edges.tolist(), 'volumes': volumes.tolist()}
