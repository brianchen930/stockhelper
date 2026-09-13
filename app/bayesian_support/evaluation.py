"""Out-of-sample metrics and descriptive diagnostics, without tuning on results."""
import json
import math
import numpy as np
import pandas as pd

from .config import BayesConfig, FEATURE_SPECS
from .features import bucket_value


def _evaluated(predictions):
    data = predictions.loc[predictions.actual_label.isin(['success', 'failure'])].copy()
    valid = pd.to_numeric(data.predicted_probability, errors='coerce')
    baseline = pd.to_numeric(data.baseline_probability, errors='coerce')
    return data.loc[valid.between(0, 1) & baseline.between(0, 1)]


def probability_metrics(y, p, epsilon=1e-15):
    if not len(y):
        return dict(brier_score=None, log_loss=None, accuracy=None)
    y, p = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    if not np.isfinite(p).all() or not ((p >= 0) & (p <= 1)).all():
        raise ValueError('Probabilities must be finite and within [0,1]')
    clipped = np.clip(p, epsilon, 1 - epsilon)
    return dict(brier_score=float(np.mean((p - y) ** 2)),
                log_loss=float(-np.mean(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))),
                accuracy=float(np.mean((p >= .5) == y)))


def evaluate_predictions(predictions, config=None):
    config = config or BayesConfig()
    data = _evaluated(predictions)
    y = data.actual_label.eq('success').astype(int)
    return dict(evaluated_count=len(data), total_predictions=len(predictions),
                neutral_count=int(predictions.actual_label.eq('neutral').sum()),
                warmup_count=int(predictions.model_status.eq('warmup').sum()),
                bayesian=probability_metrics(y, data.predicted_probability, config.log_loss_epsilon),
                baseline=probability_metrics(y, data.baseline_probability, config.log_loss_epsilon))


def calibration_summary(predictions, config=None):
    config = config or BayesConfig()
    data = _evaluated(predictions)
    records = []
    for index, (low, high) in enumerate(zip(config.calibration_edges, config.calibration_edges[1:])):
        p = data.predicted_probability
        group = data.loc[p.ge(low) & (p.le(high) if index == len(config.calibration_edges) - 2 else p.lt(high))]
        records.append(dict(probability_low=low, probability_high=high, prediction_count=len(group),
            average_predicted_probability=float(group.predicted_probability.mean()) if len(group) else None,
            actual_success_rate=float(group.actual_label.eq('success').mean()) if len(group) else None))
    return pd.DataFrame(records)


def yearly_summary(predictions, config=None):
    config = config or BayesConfig()
    data = _evaluated(predictions)
    records = []
    years = pd.to_datetime(data.event_date, utc=True).dt.tz_convert('Asia/Taipei').dt.year
    for year, group in data.groupby(years):
        y = group.actual_label.eq('success').astype(int)
        records.append(dict(year=int(year), event_count=len(group), actual_success_rate=float(y.mean()),
            average_predicted_probability=float(group.predicted_probability.mean()),
            **probability_metrics(y, group.predicted_probability, config.log_loss_epsilon)))
    return pd.DataFrame(records, columns=['year', 'event_count', 'actual_success_rate', 'average_predicted_probability', 'brier_score', 'log_loss', 'accuracy'])


def source_statistics(events):
    def families(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return []
        if not isinstance(value, list):
            return []
        output = set()
        for name in value:
            if 'vwap' in name:
                output.add('vwap')
            elif name.startswith('swing'):
                output.add('swing')
            elif name.startswith('volume_profile'):
                output.add('volume_profile')
            elif name == 'kmeans':
                output.add('kmeans')
        return sorted(output)
    groups = events.get('support_sources', pd.Series(index=events.index, dtype='object')).map(families)
    records = []
    choices = [('family', family, groups.map(lambda values: family in values)) for family in ('swing', 'volume_profile', 'vwap', 'kmeans')]
    combinations = groups.map(lambda values: '+'.join(values) or 'unknown')
    choices += [('combination', value, combinations.eq(value)) for value in sorted(combinations.unique())]
    for kind, name, mask in choices:
        data = events.loc[mask]
        s, f = int(data.label.eq('success').sum()), int(data.label.eq('failure').sum())
        records.append(dict(kind=kind, source=name, event_count=len(data), success_count=s, failure_count=f,
                            neutral_count=int(data.label.eq('neutral').sum()), resolved_count=s + f,
                            success_given_source=s / (s + f) if s + f else None))
    return pd.DataFrame(records)


def dependence_summary(events, config=None):
    """Conditional Cramer's V, reported separately for Success and Failure."""
    config = config or BayesConfig()
    records = []
    for label in ('success', 'failure'):
        data = events.loc[events.label.eq(label)]
        buckets = {name: data.get(FEATURE_SPECS[name]['raw'], pd.Series(index=data.index, dtype='object')).map(
            lambda value, name=name: bucket_value(name, value)) for name in config.active_features}
        for i, left in enumerate(config.active_features):
            for right in config.active_features[i + 1:]:
                table = pd.crosstab(buckets[left], buckets[right]).to_numpy(dtype=float)
                n = float(table.sum())
                divisor = min(table.shape) - 1 if table.ndim == 2 else 0
                value = None
                if n > 0 and divisor > 0:
                    expected = table.sum(axis=1)[:, None] * table.sum(axis=0)[None, :] / n
                    value = math.sqrt(float(((table - expected) ** 2 / expected).sum()) / (n * divisor))
                records.append(dict(label=label, feature_a=left, feature_b=right, sample_count=int(n), cramers_v=value))
    return pd.DataFrame(records)
