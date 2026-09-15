"""A/B probabilities on existing OOS predictions; never download or fit.

Strict mode requires versions collected before prediction. Historical imports
require strict=False and are explicitly marked as revised-history assumptions.
"""
import pandas as pd
from dataclasses import asdict
from .config import DEFAULT_CONFIG
from .features import build_context
from .adjustment import adjust_probability


def attach_institutional_backtest(predictions, store, *, strict=True, config=DEFAULT_CONFIG):
    records = []
    for row in predictions.to_dict('records'):
        cutoff = row['event_date']
        context = build_context(store.history(str(row['symbol']), cutoff, strict=strict), cutoff, config)
        base = row.get('predicted_probability')
        base = None if base is None or pd.isna(base) else float(base)
        row.update(base_support_probability=base,
                   adjusted_support_probability=adjust_probability(base, context, config=config),
                   institutional_score=context['institutional_score'],
                   institutional_level=context['institutional_level'],
                   institutional_features=context['features'],
                   latest_available_institutional_date=context['latest_available_institutional_date'],
                   availability_policy='observed_point_in_time' if strict else 'next_day_assumption_revised_history',
                   adjustment_method='provisional_log_odds_v1', calibration_status='uncalibrated',
                   adjustment_config=asdict(config))
        records.append(row)
    return pd.DataFrame(records)
