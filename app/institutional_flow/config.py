"""Provisional policy: tune on out-of-sample events before claiming calibration."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FlowConfig:
    streak_days: int = 3
    cumulative_ratio: float = .03
    daily_ratio: float = .05
    foreign_weight: float = 1.
    trust_weight: float = 1.
    consensus_bonus: float = 1.
    mixed_confidence: float = .35
    score_limit: float = 6.
    bullish_threshold: float = 2.
    strong_threshold: float = 4.
    log_odds_per_point: float = .12
    history_days: int = 45
    max_age_days: int = 10
    timeout_seconds: float = 10.

    def __post_init__(self):
        positive = ('streak_days', 'cumulative_ratio', 'daily_ratio', 'score_limit',
                    'bullish_threshold', 'strong_threshold', 'history_days', 'max_age_days', 'timeout_seconds')
        nonnegative = ('foreign_weight', 'trust_weight', 'consensus_bonus', 'log_odds_per_point')
        if any(not math.isfinite(getattr(self, k)) or getattr(self, k) <= 0 for k in positive):
            raise ValueError('Thresholds and windows must be finite and positive')
        if any(not math.isfinite(getattr(self, k)) or getattr(self, k) < 0 for k in nonnegative):
            raise ValueError('Weights must be finite and nonnegative')
        if not 0 <= self.mixed_confidence <= 1 or not self.bullish_threshold < self.strong_threshold <= self.score_limit:
            raise ValueError('Invalid confidence or level thresholds')


DEFAULT_CONFIG = FlowConfig()
LEVEL_LABELS = dict(STRONG_SUPPORT='強力支撐', BULLISH='偏多', NEUTRAL='中性',
                    BEARISH='偏空', STRONG_PRESSURE='強力壓力', MIXED='方向分歧', UNKNOWN='資料不足')
