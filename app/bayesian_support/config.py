"""All model policy and discretization definitions; probabilities use fractions."""
from dataclasses import dataclass
import math

MODEL_VERSION = 'bayes_support_v1'
BAYES_SMOOTHING_ALPHA = 1.0
MIN_BAYES_TRAINING_EVENTS = 100
MIN_BUCKET_EVENTS = 10

# One representation per evidence family: no historical_touch_count alongside
# current_touch_number, no volume_ratio alongside volume_level. Age/source flags
# remain descriptive research, not additional correlated active evidence.
ACTIVE_BAYES_FEATURES = (
    'current_touch_number_bucket', 'touch_rebound_trend', 'touch_volume_level',
    'volatility_level', 'support_source_count_bucket', 'distance_to_support_atr_bucket',
    'zone_width_atr_bucket', 'bars_since_last_touch_bucket',
)
FEATURE_SPECS = {
    'current_touch_number_bucket': dict(raw='current_touch_number', categories=['1', '2', '3', '4+'], edges=[1, 2, 3], right=True, minimum=1, integer=True),
    'touch_rebound_trend': dict(raw='touch_rebound_trend', categories=['strengthening', 'stable', 'weakening']),
    'touch_volume_level': dict(raw='touch_volume_level', categories=['low', 'normal', 'elevated', 'spike']),
    'volatility_level': dict(raw='volatility_level', categories=['低波動', '中等波動', '高波動', '極高波動']),
    'support_source_count_bucket': dict(raw='support_source_count', categories=['1', '2', '3', '4+'], edges=[1, 2, 3], right=True, minimum=1, integer=True),
    'distance_to_support_atr_bucket': dict(raw='distance_to_support_atr', categories=['<=0.25', '(0.25,0.5]', '(0.5,1]', '>1'], edges=[0.25, 0.5, 1.0], right=True, minimum=0),
    'zone_width_atr_bucket': dict(raw='zone_width_atr', categories=['<0.5', '[0.5,1)', '[1,1.5)', '>=1.5'], edges=[0.5, 1.0, 1.5], right=False, minimum=0),
    'bars_since_last_touch_bucket': dict(raw='bars_since_last_touch', categories=['0-5', '6-20', '21-60', '>60'], edges=[5, 20, 60], right=True, minimum=0, integer=True),
    'support_age_bars_bucket': dict(raw='support_age_bars', categories=['0-20', '21-60', '61-120', '>120'], edges=[20, 60, 120], right=True, minimum=0, integer=True),
}
FORBIDDEN_OUTCOME_COLUMNS = frozenset({
    'label', 'actual_label', 'target', 'label_available_date', 'future_max_return', 'future_min_return',
    'future_max_price', 'future_min_price', 'future_max_return_pct', 'future_min_return_pct',
    'max_rebound_atr', 'max_breakdown_atr', 'success_bar', 'failure_bar', 'bars_to_success',
    'bars_to_failure', 'same_bar_ambiguous', 'future_3bar_avg_volume', 'future_volume_ratio',
    'post_touch_volume_avg', 'post_pre_volume_ratio', 'rebound_volume_confirmation',
    'breakdown_volume', 'breakdown_volume_ma20', 'breakdown_volume_ratio', 'breakdown_volume_level',
})
PREDICTOR_COLUMNS = tuple(dict.fromkeys(spec['raw'] for spec in FEATURE_SPECS.values()))


def validate_predictor_columns(columns):
    for name in columns:
        if name in FORBIDDEN_OUTCOME_COLUMNS or name.startswith(('future_', 'post_', 'breakdown_')):
            raise ValueError(f'Forbidden outcome predictor (data leakage): {name}')
        if name not in PREDICTOR_COLUMNS and name not in FEATURE_SPECS:
            raise ValueError(f'Predictor is not in the explicit allowlist: {name}')
    if len(columns) != len(set(columns)):
        raise ValueError('Duplicate predictors would double-count evidence')


@dataclass(frozen=True)
class BayesConfig:
    alpha: float = BAYES_SMOOTHING_ALPHA
    min_training_events: int = MIN_BAYES_TRAINING_EVENTS
    min_bucket_events: int = MIN_BUCKET_EVENTS
    active_features: tuple = ACTIVE_BAYES_FEATURES
    positive_lr: float = 1.1
    negative_lr: float = 0.9
    confidence_edges: tuple = (0.4, 0.6, 0.75, 0.9)
    confidence_labels: tuple = ('low', 'uncertain', 'moderate', 'high', 'very_high')
    calibration_edges: tuple = tuple(i / 10 for i in range(11))
    log_loss_epsilon: float = 1e-15

    def __post_init__(self):
        validate_predictor_columns(self.active_features)
        if any(name not in FEATURE_SPECS for name in self.active_features):
            raise ValueError('active_features must use configured feature/bucket names')
        if not math.isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError('alpha must be finite and positive')
        for name in ('min_training_events', 'min_bucket_events'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')
        if not 0 < self.negative_lr < 1 < self.positive_lr < float('inf'):
            raise ValueError('LR thresholds must bracket 1')
        if len(self.confidence_labels) != len(self.confidence_edges) + 1 or not all(
                0 < a < b < 1 for a, b in zip(self.confidence_edges, self.confidence_edges[1:])):
            raise ValueError('Invalid confidence boundaries')
        if self.calibration_edges[0] != 0 or self.calibration_edges[-1] != 1 or any(
                a >= b for a, b in zip(self.calibration_edges, self.calibration_edges[1:])):
            raise ValueError('Calibration edges must increase from 0 to 1')
        if not 0 < self.log_loss_epsilon < .5:
            raise ValueError('Invalid log-loss clipping epsilon')
