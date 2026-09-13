"""Support-event research definitions. ATR amounts use the frozen entry ATR."""
from dataclasses import dataclass, field
import math

TOUCH_ATR_THRESHOLD = 0.25
EVENT_LOOKAHEAD_BARS = 10
SUCCESS_REBOUND_ATR = 2.0
FAILURE_BREAKDOWN_ATR = 0.5
SUPPORT_EVENT_EXIT_ATR = 1.0
SUPPORT_EVENT_EXIT_BARS = 2
TOUCH_EXIT_ATR = 0.75
TOUCH_EXIT_BARS = 2
TOUCH_REACTION_LOOKAHEAD = 5
TOUCH_WEAKENING_RATIO = 0.7
VOLUME_MA_PERIOD = 20
VOLUME_REACTION_BARS = 3
VOLUME_RATIO_LOW = 0.8
VOLUME_RATIO_HIGH = 1.2
VOLUME_RATIO_SPIKE = 1.8


@dataclass(frozen=True)
class TouchVolumeConfig:
    touch_exit_atr: float = TOUCH_EXIT_ATR
    touch_exit_bars: int = TOUCH_EXIT_BARS
    reaction_lookahead: int = TOUCH_REACTION_LOOKAHEAD
    touch_success_atr: float = 1.0
    touch_failure_atr: float = 0.5
    weakening_ratio: float = TOUCH_WEAKENING_RATIO
    volume_ma_period: int = VOLUME_MA_PERIOD
    volume_reaction_bars: int = VOLUME_REACTION_BARS
    pre_volume_bars: int = 5
    volume_ratio_low: float = VOLUME_RATIO_LOW
    volume_ratio_high: float = VOLUME_RATIO_HIGH
    volume_ratio_spike: float = VOLUME_RATIO_SPIKE
    rebound_volume_strong: float = 1.2
    rebound_volume_weak: float = 1.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for name in ('touch_exit_bars', 'reaction_lookahead', 'volume_ma_period',
                     'volume_reaction_bars', 'pre_volume_bars'):
            if type(getattr(self, name)) is not int:
                raise ValueError(f'{name} must be a positive integer')
        if not 0 < self.weakening_ratio < 1:
            raise ValueError('weakening_ratio must be between 0 and 1')
        if not self.volume_ratio_low < self.volume_ratio_high < self.volume_ratio_spike:
            raise ValueError('volume level thresholds must increase')
        if self.rebound_volume_weak >= self.rebound_volume_strong:
            raise ValueError('weak confirmation threshold must be below strong')


@dataclass(frozen=True)
class SupportEventConfig:
    touch_atr_threshold: float = TOUCH_ATR_THRESHOLD
    lookahead_bars: int = EVENT_LOOKAHEAD_BARS
    success_rebound_atr: float = SUCCESS_REBOUND_ATR
    failure_breakdown_atr: float = FAILURE_BREAKDOWN_ATR
    exit_atr: float = SUPPORT_EVENT_EXIT_ATR
    exit_bars: int = SUPPORT_EVENT_EXIT_BARS
    min_history: int = 60
    zone_match_atr: float = 0.5
    same_bar_policy: str = 'failure'
    evidence: TouchVolumeConfig = field(default_factory=TouchVolumeConfig)

    def __post_init__(self):
        if not isinstance(self.evidence, TouchVolumeConfig):
            raise ValueError('evidence must be TouchVolumeConfig')
        for name in ('lookahead_bars', 'exit_bars', 'min_history'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        for name in ('touch_atr_threshold', 'success_rebound_atr',
                     'failure_breakdown_atr', 'exit_atr', 'zone_match_atr'):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f'{name} must be finite and nonnegative')
        if self.success_rebound_atr <= 0:
            raise ValueError('success_rebound_atr must be positive')
        if self.same_bar_policy not in ('success', 'failure'):
            raise ValueError('same_bar_policy must be success or failure')
