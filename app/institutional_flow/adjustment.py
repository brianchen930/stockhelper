"""Uncalibrated provisional log-odds layer; never replaces Bayesian evidence."""
import math
from app.bayesian_support.model import stable_logistic
from app.bayesian_support.rating import RATINGS, rate_support_probability
from .config import DEFAULT_CONFIG


def adjust_probability(base, context, *, side='support', config=DEFAULT_CONFIG):
    if side not in ('support', 'resistance'):
        raise ValueError('side must be support or resistance')
    if base is None:
        return None
    if not math.isfinite(base) or not 0 <= base <= 1:
        raise ValueError('Probability must be finite and within [0,1]')
    if context['institutional_level'] == 'UNKNOWN' or base in (0., 1.):
        return base
    delta = config.log_odds_per_point * context['institutional_score'] * context['confidence']
    if side == 'resistance':
        delta = -delta  # Probability of resistance HOLDING, not breaking out.
    if delta == 0:
        return base
    return stable_logistic(math.log(base) - math.log1p(-base) + delta)


def adjust_candidate(candidate, context, config=DEFAULT_CONFIG):
    result = candidate['result']
    base = result.get('posterior_success_probability')
    display = candidate.get('display') or rate_support_probability(result).to_dict()
    adjusted = adjust_probability(base, context, config=config) if result.get('model_status') == 'ready' else base
    level = RATINGS[sum(adjusted >= edge for edge in display['thresholds'])] if adjusted is not None and result.get('model_status') == 'ready' else '資料不足'
    candidate.update(base_support_probability=base, adjusted_support_probability=adjusted,
                     adjusted_support_level=level, institutional_score=context['institutional_score'],
                     institutional_level=context['institutional_level'],
                     adjustment_method='provisional_log_odds_v1', calibration_status='uncalibrated')
    return candidate
