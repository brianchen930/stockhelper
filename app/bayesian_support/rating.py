"""Presentation-only relative ratings; never a calibrated absolute success rate.

Build references OFFLINE from auditable walk-forward rows. Neutral predictions
are included: their posterior was issued out of sample, independent of outcome.
"""
from dataclasses import asdict, dataclass
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from .features import finite

VERY_LOW_MAX, LOW_MAX, MEDIUM_MAX, HIGH_MAX = .30, .40, .50, .60
MIN_RATING_DISTRIBUTION_SAMPLES = 100
SHOW_BAYESIAN_PERCENT_IN_NORMAL_MODE = False
ZONE_OVERLAP_THRESHOLD = .7
RATINGS = ('極低', '低', '中', '高', '極高')
RATING_DESCRIPTIONS = dict(zip(RATINGS, (
    '歷史條件明顯不利，支撐失敗風險較高。', '歷史條件偏弱，支撐可靠度不足。',
    '歷史條件中性，支撐效果仍需觀察。', '歷史條件偏有利，支撐成功可能性較佳。',
    '歷史條件明顯有利，屬較強支撐事件。')))
QUANTILES = (10, 20, 25, 40, 50, 60, 75, 80, 90)


def _oos_rows(predictions_df):
    required = {'event_date', 'training_available_until', 'model_status', 'predicted_probability',
                'symbol', 'timeframe', 'support_low', 'support_high'}
    if not required.issubset(predictions_df.columns):
        raise ValueError('Rating reference requires auditable walk-forward prediction columns')
    rows = predictions_df.loc[predictions_df.model_status.eq('ready')].copy().reset_index(drop=True)
    probability = pd.to_numeric(rows.predicted_probability, errors='coerce')
    rows = rows.loc[probability.between(0, 1)].copy()
    rows['predicted_probability'] = probability.loc[rows.index]
    rows['event_date'] = pd.to_datetime(rows.event_date, utc=True, errors='coerce')
    known = pd.to_datetime(rows.training_available_until, utc=True, errors='coerce')
    if not (known < rows.event_date).all():
        raise ValueError('Reference contains in-sample or unverifiable predictions')
    keys = ['symbol', 'timeframe', 'event_date', 'support_low', 'support_high']
    if rows.duplicated(keys).any():
        raise ValueError('Duplicate OOS events in rating reference')
    return rows


def analyze_posterior_distribution(predictions_df):
    values = _oos_rows(predictions_df).predicted_probability
    result = dict(count=len(values), mean=None, median=None, std=None, min=None, max=None)
    result.update({f'p{q}': None for q in QUANTILES})
    if len(values):
        result.update(mean=float(values.mean()), median=float(values.median()),
                      std=float(values.std(ddof=0)), min=float(values.min()), max=float(values.max()))
        result.update({f'p{q}': float(values.quantile(q / 100)) for q in QUANTILES})
    return result


def validate_thresholds(thresholds):
    values = tuple(finite(v) for v in thresholds)
    if len(values) != 4 or any(v is None or not 0 < v < 1 for v in values) or any(a >= b for a, b in zip(values, values[1:])):
        raise ValueError('Rating thresholds must be four strictly increasing probabilities inside (0,1)')
    return values


def build_rating_reference(predictions_df, *, strategy='distribution', fixed_thresholds=None):
    if strategy not in ('distribution', 'fixed'):
        raise ValueError('Unknown rating strategy')
    fixed = validate_thresholds(fixed_thresholds or (VERY_LOW_MAX, LOW_MAX, MEDIUM_MAX, HIGH_MAX))
    rows = _oos_rows(predictions_df)
    stats = analyze_posterior_distribution(predictions_df)
    thresholds, selected, reason = fixed, 'fixed' if strategy == 'fixed' else 'fixed_fallback', None
    if strategy == 'distribution':
        reason = 'insufficient_distribution_samples'
        if stats['count'] >= MIN_RATING_DISTRIBUTION_SAMPLES:
            try:
                thresholds = validate_thresholds([stats[f'p{q}'] for q in (20, 40, 60, 80)])
                selected, reason = 'distribution', None
            except ValueError:
                reason = 'degenerate_quantiles'
    return dict(version='support_rating_v1', rating_strategy=selected, thresholds=list(thresholds),
                fixed_thresholds=list(fixed), distribution=stats, fallback_reason=reason,
                minimum_samples=MIN_RATING_DISTRIBUTION_SAMPLES,
                reference_end=None if rows.empty else rows.event_date.max().isoformat(),
                symbols=sorted(rows.symbol.astype(str).unique().tolist()),
                calibration_status='uncalibrated', interpretation='relative_model_prediction_position')


@dataclass
class SupportProbabilityDisplayResult:
    posterior_probability: float | None
    rating: str
    rating_description: str
    rating_strategy: str
    thresholds: list
    model_status: str
    calibration_status: str = 'uncalibrated'

    def to_dict(self):
        return asdict(self)


def rate_support_probability(result, reference=None):
    result = result.to_dict() if hasattr(result, 'to_dict') else result
    reference = reference or {}
    thresholds = validate_thresholds(reference.get('thresholds', (VERY_LOW_MAX, LOW_MAX, MEDIUM_MAX, HIGH_MAX)))
    probability = finite(result.get('posterior_success_probability'))
    valid = probability is not None and 0 <= probability <= 1 and result.get('model_status') == 'ready'
    rating = RATINGS[sum(probability >= edge for edge in thresholds)] if valid else '資料不足'
    return SupportProbabilityDisplayResult(probability, rating, RATING_DESCRIPTIONS.get(rating, ''),
        reference.get('rating_strategy', 'fixed_fallback'), list(thresholds),
        result.get('model_status', 'unavailable'), reference.get('calibration_status', 'uncalibrated'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('predictions', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--strategy', choices=['distribution', 'fixed'], default='distribution')
    args = parser.parse_args()
    reference = build_rating_reference(pd.read_csv(args.predictions, dtype={'symbol': str}), strategy=args.strategy)
    reference['source_sha256'] = hashlib.sha256(args.predictions.read_bytes()).hexdigest()
    reference['source_path'] = str(args.predictions)
    with args.output.open('x', encoding='utf-8') as file:
        json.dump(reference, file, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(reference, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
