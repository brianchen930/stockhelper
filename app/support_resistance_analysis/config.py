"""All numerical policy settings; percentages here are fractions (0.01 = 1%)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SupportResistanceConfig:
    lookback: int = 120
    swing_window: int = 3
    swing_min_pct: float = 0.005
    swing_atr_factor: float = 0.5
    atr_period: int = 14
    vp_window: int = 60
    vp_bins: int = 40
    vp_bin_size: float | None = None
    vp_max_bins: int = 500
    hvn_fraction: float = 0.3
    vwap_window: int = 20
    kmeans_max_clusters: int = 5
    kmeans_min_points: int = 3
    kmeans_iterations: int = 100
    kmeans_convergence: float = 1e-8
    merge_tolerance_pct: float = 0.01
    zone_padding_pct: float = 0.002
    neutral_tolerance_pct: float = 0.002
    reaction_pct: float = 0.01
    reaction_bars: int = 5
    touch_saturation: float = 4.0
    volume_saturation: float = 3.0
    recency_half_life: float = 60.0
    recency_floor: float = 0.25
    max_bar_range_ratio: float = 3.0
    max_close_jump_ratio: float = 3.0
    max_distance_pct: float = 0.20
    max_support_zones: int = 3
    max_resistance_zones: int = 3
    max_active_zones: int = 3
    ranking_strength_weight: float = 0.03
    method_weights: dict[str, float] = field(default_factory=lambda: {
        'swing': 1.0, 'volume_profile': 1.0, 'vwap': 1.0, 'kmeans': 1.0})
    score_weights: dict[str, float] = field(default_factory=lambda: {
        'consensus': 0.55, 'touch': 0.20, 'volume': 0.10, 'recency': 0.15})
    strength_thresholds: tuple = ((0.0, 'weak'), (4.0, 'medium'), (6.0, 'strong'), (8.0, 'very_strong'))

    def __post_init__(self):
        import math
        for name, value in vars(self).items():
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                raise ValueError(f'{name} must be finite and nonnegative')
        for name in ('lookback', 'swing_window', 'atr_period', 'vp_window', 'vp_bins',
                     'vp_max_bins', 'vwap_window', 'kmeans_max_clusters', 'kmeans_min_points',
                     'kmeans_iterations', 'reaction_bars'):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        for name in ('touch_saturation', 'volume_saturation', 'recency_half_life'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if self.vp_bin_size is not None and self.vp_bin_size <= 0:
            raise ValueError('vp_bin_size must be positive')
        for name in ('max_support_zones', 'max_resistance_zones', 'max_active_zones'):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f'{name} must be an integer')
        if not 0 <= self.recency_floor <= 1 or not 0 <= self.hvn_fraction <= 1:
            raise ValueError('recency_floor and hvn_fraction must be between 0 and 1')
        if min(self.max_bar_range_ratio, self.max_close_jump_ratio) < 1:
            raise ValueError('price quality ratios must be at least 1')
        if set(self.method_weights) != {'swing', 'volume_profile', 'vwap', 'kmeans'}:
            raise ValueError('method_weights must specify all four method families')
        if set(self.score_weights) != {'consensus', 'touch', 'volume', 'recency'}:
            raise ValueError('score_weights must specify all four score components')
        if not self.strength_thresholds or any(not math.isfinite(t) or t < 0 or t > 10
                                               for t, label in self.strength_thresholds):
            raise ValueError('strength thresholds must be within 0..10')
        for weights in (self.method_weights, self.score_weights):
            if not weights or any(not math.isfinite(v) or v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
                raise ValueError('weights must be finite, nonnegative, with positive total')
