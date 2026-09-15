"""Optional integration after the existing detector and Bayesian inference."""
import logging
from dataclasses import asdict
from datetime import datetime
from .features import TAIPEI, unknown, taipei_time
from .service import InstitutionalFlowService
from .adjustment import adjust_candidate

logger = logging.getLogger(__name__)


def attach_institutional_flow(result, symbol, *, service=None, as_of=None, replay=False):
    sr = result.get('support_resistance')
    if not isinstance(sr, dict) or sr.get('error'):
        return
    service_instance = service
    try:
        service_instance = service or InstitutionalFlowService()
        context = service_instance.context(symbol, as_of=as_of, replay=replay)
    except Exception:
        context = unknown()
    timestamp = taipei_time(as_of).isoformat() if as_of is not None else datetime.now(TAIPEI).isoformat()
    sr['institutional_context'] = context
    from .config import DEFAULT_CONFIG
    context['adjustment_config'] = asdict(getattr(service_instance, 'config', DEFAULT_CONFIG))
    score = context['institutional_score']
    sr['support_institutional_adjustment'] = 'favorable' if score is not None and score > 0 else 'adverse' if score is not None and score < 0 else context['institutional_level']
    sr['resistance_institutional_adjustment'] = 'breakout_more_likely' if score is not None and score > 0 else 'holding_more_likely' if score is not None and score < 0 else context['institutional_level']
    predictions = sr.get('bayesian_support', [])
    selected = sr.get('bayesian_support_selected')
    if selected is not None and not any(c is selected for c in predictions):
        predictions = [*predictions, selected]
    for candidate in predictions:
        adjust_candidate(candidate, context, getattr(service_instance, 'config', DEFAULT_CONFIG))
        if service_instance is not None and not replay and as_of is None:
            try:
                snapshot = dict(context, prediction_result=candidate['result'],
                                base_display=candidate.get('display'), price_as_of=sr.get('as_of'))
                service_instance.store.save_event(symbol.split('.')[0], timestamp, candidate, snapshot)
            except Exception as error:
                logger.warning('Institutional prediction snapshot unavailable: %s', type(error).__name__)
    if selected:
        for key in ('base_support_probability', 'institutional_score', 'institutional_level',
                    'adjusted_support_probability', 'adjusted_support_level'):
            sr[key] = selected[key]
    from app.support_resistance_analysis.formatting import format_support_resistance_output
    result['support_resistance_text'] = format_support_resistance_output(sr)
