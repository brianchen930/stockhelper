"""High-confidence risk escalation from existing decision evidence, without IO."""
from app.decision_engine import ReasonCode as R, HolderActionState as H
from app.decision_evidence import valid as valid_holder_evidence
from app.decision_zones import valid_zone
from app.market_data import is_finite_number
from app.position_status import PositionStatus, read_position_status


def evaluate_critical_event(d, c, config):
    """Require a completed observation and current evidence, never retained labels.

    Engine evidence owns support confirmation and MACD classification. This filter adds
    confidence requirements for bypass; it does not change their calculations.
    """
    rules, dimensions = [], []
    eligible = bool(c.data_valid and c.observation_complete and c.observation_time
                    and is_finite_number(c.atr) and c.atr > 0 and d.state_basis != 'STALE_DATA')
    zone = None
    strength = None
    from app.holder_structure import structural_break
    if structural_break(c):
        zone = c.structural_support_zone
        strength = config.min_support_strength if c.breakout_event else (zone or {}).get('strength_score')
    broken = bool(valid_zone(zone) and is_finite_number(c.current_price) and c.current_price < zone['low'])
    important_break = broken and is_finite_number(strength) and strength >= config.min_support_strength
    # Scoring tolerates an absent magnitude; bypass deliberately requires it.
    acceleration = (R.BEARISH_MACD_ACCELERATION in d.entry_reasons
                    and is_finite_number(c.macd_histogram_change_atr)
                    and c.macd_histogram_change_atr <= -config.bearish_acceleration_atr)
    evidence = d.current_trigger_evidence
    raw_holder = evidence.get('holder_state')
    holding = read_position_status(c.position_status) == PositionStatus.HOLDING
    if eligible:
        if important_break:
            rules.append('IMPORTANT_SUPPORT_CONFIRMED_BREAK')
        if c.medium_term_direction < 0 and acceleration:
            rules.append('MEDIUM_BEARISH_WITH_MACD_ACCELERATION')
        if (holding and broken and raw_holder in (H.REDUCE_EXPOSURE, H.EXIT_CONDITION_APPROACHING)
                and valid_holder_evidence(evidence, raw_holder)
                and (c.institutional_level in ('BEARISH', 'STRONG_PRESSURE') or acceleration)):
            rules.append('HOLDER_' + raw_holder)
        # Count strong, independent dimensions only, using the existing net
        # dimension totals. RSI/KD, short trend, ATR and minor breaks cannot
        # provide extra votes. No second weighted score is computed here.
        strong = dict(structure=important_break, trend=c.medium_term_direction < 0,
                      momentum=acceleration, institutional=c.institutional_level == 'STRONG_PRESSURE',
                      relative_market=R.RELATIVE_WEAKNESS in d.entry_reasons)
        totals = d.trade_evidence.get('dimension_scores', {})
        dimensions = [key for key, established in strong.items() if established and totals.get(key, 0) > 0]
        if (d.trade_evidence.get('checks', {}).get('valid')
                and d.trade_evidence.get('weighted_score', 0) >= config.trade_reduce_score
                and len(dimensions) >= config.critical_min_risk_dimensions):
            rules.append('MULTIPLE_INDEPENDENT_HIGH_RISKS')
    return dict(critical_event=bool(rules), rules=rules, target_state='偏空' if rules else None,
                eligible=eligible, risk_dimensions=dimensions,
                confirmed_support_zone=dict(zone) if broken else None,
                current_holder_rule=evidence.get('matched_rule_id'))
