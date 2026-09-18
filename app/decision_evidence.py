"""Evidence emitted at the matched holder branch, separate from transition deltas."""
from copy import deepcopy
from datetime import datetime
import json

RISK_STATES = {'TIGHTEN_RISK', 'REDUCE_EXPOSURE', 'EXIT_CONDITION_APPROACHING'}
# Requirements mirror the existing branches, not a new market scoring system.
MINIMUM_TRIGGER_REQUIREMENTS = {
    'TIGHTEN_RISK': ('SUPPORT_BREAK', 'LOW_SUPPORT_WITH_PRESSURE', 'RISK_SCORE_THRESHOLD'),
    'REDUCE_EXPOSURE': ('BROKEN_SUPPORT_WITH_CONFIRMING_RISK',),
    'EXIT_CONDITION_APPROACHING': ('BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE',),
}
LABELS = {
    'CONFIRMED_SUPPORT_BREAK': '目前支撐已確認失守',
    'PREVIOUS_SUPPORT_BREAK_CONFIRMED': '原支撐已確認失守',
    'BEARISH_MACD_ACCELERATION': 'MACD 空方動能增強',
    'STRONG_INSTITUTIONAL_PRESSURE': '法人維持強力壓力',
    'INSTITUTIONAL_BEARISH': '法人偏空',
    'MEDIUM_TERM_BEARISH': '中期方向偏空',
    'LOW_SUPPORT_PROBABILITY': '支撐成功率偏低',
    'RISK_SCORE_THRESHOLD': '多項風險累積達到警戒門檻',
    'EXTREME_VOLATILITY': '波動極高', 'HIGH_VOLATILITY': '波動偏高',
    'BEARISH_SHORT_TREND': '短期方向偏空', 'BOTH_TRENDS_BEARISH': '短中期方向皆偏空',
    'MINOR_SUPPORT_BREAK': '支撐出現輕微跌破', 'RESISTANCE_REJECTION': '壓力區遇阻',
    'RELATIVE_WEAKNESS': '相對大盤偏弱', 'OVEREXTENDED': '價格過熱',
}


def compact_zone(zone):
    return {k: zone[k] for k in ('stable_zone_id', 'zone_id', 'low', 'high', 'status', 'current_role', 'role') if k in zone} if zone else None


def identity(zone):
    return (zone or {}).get('stable_zone_id') or (zone or {}).get('zone_id')


def matches(a, b):
    if identity(a) or identity(b):
        return bool(identity(a) and identity(a) == identity(b))
    return bool(a and b and a.get('low') == b.get('low') and a.get('high') == b.get('high'))


def item(code, priority='MEDIUM', **details):
    return dict(code=code, priority=priority, established=True, **details)


def record_evidence(d, c, rule, contributions, bearish_macd, config):
    primary, supporting, zone = None, [], None
    if rule in ('SUPPORT_BREAK', 'BROKEN_SUPPORT_WITH_CONFIRMING_RISK', 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE'):
        current = 'SUPPORT_BREAK' in d.holder_reasons
        zone = compact_zone(c.active_support_zone if current else c.previous_support_zone)
        primary = item('CONFIRMED_SUPPORT_BREAK' if current else 'PREVIOUS_SUPPORT_BREAK_CONFIRMED', 'HIGH', zone=zone)
        if rule != 'SUPPORT_BREAK':
            if c.institutional_level in ('BEARISH', 'STRONG_PRESSURE'):
                supporting.append(item('STRONG_INSTITUTIONAL_PRESSURE' if c.institutional_level == 'STRONG_PRESSURE' else 'INSTITUTIONAL_BEARISH'))
            if bearish_macd:
                supporting.append(item('BEARISH_MACD_ACCELERATION'))
            if rule == 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE':
                supporting.insert(0, item('MEDIUM_TERM_BEARISH'))
    elif rule == 'LOW_SUPPORT_WITH_PRESSURE':
        primary = item('LOW_SUPPORT_PROBABILITY')
        supporting = [item('STRONG_INSTITUTIONAL_PRESSURE')]
    elif rule == 'RISK_SCORE_THRESHOLD':
        primary = item('RISK_SCORE_THRESHOLD', 'HIGH', actual=d.risk_score, threshold=config.tighten_risk_score)
        supporting = [item(r['code'], points=r['points']) for r in contributions]
    d.current_trigger_evidence = dict(version=1, holder_state=str(d.holder_action), matched_rule_id=rule,
        primary_trigger=primary, supporting_evidence=supporting,
        triggered_conditions=([primary] if primary else []) + supporting,
        trigger_zone=zone, current_defense_zone=compact_zone(c.active_support_zone),
        observation_time=c.observation_time,
        confirmation_context={'breakout': 'CONFIRMED_BREAKOUT' in (c.resistance_status, c.previous_resistance_status),
                              'medium_positive': c.medium_term_direction > 0,
                              'bullish_momentum': c.macd_momentum == 'bullish_strengthening',
                              'expanding_volume': c.volume_state == 'EXPANDING'},
        minimum_requirement_met=str(d.holder_action) not in RISK_STATES or primary is not None,
        trade_thesis_assessment={'status': 'STRUCTURAL_RISK_APPROACHING' if rule == 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE' else 'NOT_ESTABLISHED',
                                'trade_thesis_invalidated': None,
                                'unconfirmed': ['FAILED_RECLAIM', 'PERSONAL_HOLDING_THESIS']})
    d.state_basis = 'DIRECT_RULE_MATCH'


def valid(evidence, state):
    if state not in RISK_STATES:
        return True
    if not evidence or evidence.get('holder_state') != state:
        return False
    if evidence.get('matched_rule_id') not in MINIMUM_TRIGGER_REQUIREMENTS[state]:
        return False
    primary = evidence.get('primary_trigger') or {}
    supporting = {r['code'] for r in evidence.get('supporting_evidence', []) if r.get('established')}
    code = primary.get('code')
    if not primary.get('established'):
        return False
    broken = code in ('CONFIRMED_SUPPORT_BREAK', 'PREVIOUS_SUPPORT_BREAK_CONFIRMED')
    if state == 'EXIT_CONDITION_APPROACHING':
        return broken and {'MEDIUM_TERM_BEARISH', 'BEARISH_MACD_ACCELERATION', 'STRONG_INSTITUTIONAL_PRESSURE'} <= supporting
    if state == 'REDUCE_EXPOSURE':
        return broken and bool(supporting & {'INSTITUTIONAL_BEARISH', 'STRONG_INSTITUTIONAL_PRESSURE', 'BEARISH_MACD_ACCELERATION'})
    return broken or (code == 'LOW_SUPPORT_PROBABILITY' and 'STRONG_INSTITUTIONAL_PRESSURE' in supporting) or (
        code == 'RISK_SCORE_THRESHOLD' and primary.get('actual', -1) >= primary.get('threshold', float('inf')) and bool(supporting))


def load_memory(old):
    value = old.get('holder_evidence_summary')
    if isinstance(value, dict):
        return deepcopy(value)
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        return {}


def compact_evidence(evidence):
    # Future conditions are rebuilt from this round's context, never historical evidence.
    result = deepcopy({k: v for k, v in evidence.items() if k not in ('worsen_conditions', 'improve_conditions')})
    result['current_defense_zone'] = compact_zone(result.get('current_defense_zone'))
    return result


def invalidation(evidence, c, config):
    zone = evidence.get('trigger_zone')
    lifecycle = c.zone_lifecycle_statuses.get(identity(zone), {})
    if lifecycle.get('status') in ('RECLAIMED_SUPPORT', 'RESISTANCE_TO_SUPPORT'):
        return 'TRIGGER_ZONE_RECLAIMED'
    if lifecycle.get('status') == 'ACTIVE_SUPPORT' and lifecycle.get('current_role') == 'SUPPORT':
        return 'LIFECYCLE_SUPPORT_RECONFIRMED'
    for candidate, status in ((c.active_support_zone, c.current_active_support_status if c.current_active_support_status != 'UNKNOWN' else c.support_status),
                              (c.previous_support_zone, c.previous_support_status)):
        if matches(zone, candidate):
            if status == 'RECLAIMED' or (candidate or {}).get('status') in ('RECLAIMED_SUPPORT', 'RESISTANCE_TO_SUPPORT'):
                return 'TRIGGER_ZONE_RECLAIMED'
            if (candidate or {}).get('status') == 'ACTIVE_SUPPORT' and (candidate or {}).get('current_role') == 'SUPPORT':
                return 'LIFECYCLE_SUPPORT_RECONFIRMED'
    if (c.observation_complete and c.medium_term_direction > 0 and c.macd_momentum == 'bullish_strengthening'
            and 'CONFIRMED_BREAKOUT' in (c.resistance_status, c.previous_resistance_status) and c.volume_state == 'EXPANDING'
            and evidence.get('confirmation_context') and not all(evidence['confirmation_context'].values())):
        return 'SUPERSEDED_BY_CONFIRMED_BULLISH_STRUCTURE'
    try:
        age = (datetime.fromisoformat(c.observation_time).date() - datetime.fromisoformat(evidence['observation_time']).date()).days
    except (ValueError, TypeError, KeyError):
        return 'EVIDENCE_TIME_UNAVAILABLE'
    if age >= config.retained_evidence_max_days:
        return 'EVIDENCE_MAX_AGE_REACHED'
    return None


def reconcile(d, raw, c, old, state, config):
    """Validate final holder evidence before transition analysis and persistence."""
    from app.decision_engine import HolderActionState as H
    d.consistency_warnings = list(d.consistency_warnings)
    memory = load_memory(old)
    historical = memory.get('retained_state_evidence') or memory.get('current_trigger_evidence') or {}
    d.retained_state_evidence = {}
    if not valid(raw.current_trigger_evidence, str(raw.holder_action)):
        d.consistency_warnings.append(str(raw.holder_action) + '_WITHOUT_TRIGGER_EVIDENCE')
        d.holder_action = H.HOLD_WITH_CAUTION
        d.state_basis = 'INSUFFICIENT_EVIDENCE'
        d.current_trigger_evidence = {}
        state['candidate_holder_state'] = str(d.holder_action)
        state['holder_confirmation_count'] = 0
    elif d.holder_action != raw.holder_action:
        reason = invalidation(historical, c, config) if valid(historical, str(d.holder_action)) else 'RETAINED_EVIDENCE_UNAVAILABLE'
        if reason:
            d.consistency_warnings.append(reason)
            if reason == 'RETAINED_EVIDENCE_UNAVAILABLE':
                d.consistency_warnings.append(str(d.holder_action) + '_WITHOUT_TRIGGER_EVIDENCE')
            d.holder_action = raw.holder_action
            d.state_basis = 'DIRECT_RULE_MATCH'
        else:
            d.retained_state_evidence = deepcopy(historical)
            d.retained_state_evidence['pending_confirmation_count'] = state.get('holder_confirmation_count', 0)
            d.retained_state_evidence['required_confirmations'] = config.confirmation_required
            d.state_basis = 'RETAINED_PENDING_CONFIRMATION'
    else:
        d.state_basis = raw.state_basis
        if memory.get('retained_state_evidence'):
            d.consistency_warnings.append('IMPROVEMENT_CONFIRMATION_REACHED' if state.get('holder_confirmation_count', 0) >= config.confirmation_required
                                          else 'RETAINED_EVIDENCE_SUPERSEDED_BY_CURRENT_DECISION')
    if d.state_basis != 'RETAINED_PENDING_CONFIRMATION':
        d.holder_reasons = [reason for reason in d.holder_reasons if reason != 'CONFIRMATION_PENDING']
    state['holder_state'] = str(d.holder_action)
    state['holder_evidence_summary'] = json.dumps(dict(current_trigger_evidence=compact_evidence(d.current_trigger_evidence),
        retained_state_evidence=compact_evidence(d.retained_state_evidence) if d.retained_state_evidence else {},
        state_basis=d.state_basis), ensure_ascii=False, sort_keys=True)
    return d


def evidence_text(e):
    if not e:
        return ''
    code, zone = e['code'], e.get('zone')
    if zone and code in ('CONFIRMED_SUPPORT_BREAK', 'PREVIOUS_SUPPORT_BREAK_CONFIRMED'):
        return ('原支撐 ' if code.startswith('PREVIOUS') else '目前支撐 ') + f"{zone['low']:.2f}～{zone['high']:.2f} 已確認失守"
    return LABELS.get(code, code)
