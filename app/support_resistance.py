"""Compatibility entry point; all detection lives in support_resistance_analysis."""
from dataclasses import replace
from app.support_resistance_analysis import (
    SupportResistanceConfig, SupportResistanceEngine, SupportResistanceLevel,
    format_support_resistance_output,
)


class SupportResistanceDetector(SupportResistanceEngine):
    def __init__(self, vp_window=60, vp_bins=50, fractal_n=2,
                 fractal_max_lookback=90, max_distance_pct=0.20,
                 break_threshold_pct=0.005, break_confirmation_bars=2,
                 merge_tolerance_pct=0.01, kmeans_window=60, n_clusters=5,
                 max_support_zones=3, max_resistance_zones=3,
                 proximity_threshold_pct=0.02, reaction_lookahead=5, *, config=None):
        # Old breakout/EMA policy is intentionally retired from zone detection.
        # Arguments remain accepted for existing callers; use config for new code.
        super().__init__(config or replace(SupportResistanceConfig(),
            vp_window=vp_window, vp_bins=vp_bins, swing_window=fractal_n,
            lookback=max(fractal_max_lookback, vp_window, kmeans_window),
            max_distance_pct=max_distance_pct, merge_tolerance_pct=merge_tolerance_pct,
            kmeans_max_clusters=n_clusters, max_support_zones=max_support_zones,
            max_resistance_zones=max_resistance_zones, reaction_bars=reaction_lookahead))
