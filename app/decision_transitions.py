"""Structured, inspectable transition conditions, not promises of automatic trades."""
from enum import StrEnum
from app.decision_engine import ActionState as A, DecisionConfig


class TriggerCode(StrEnum):
    SUPPORT_HOLD = 'SUPPORT_HOLD'
    BEARISH_MOMENTUM_WEAKENING = 'BEARISH_MOMENTUM_WEAKENING'
    RECLAIM_KEY_LEVEL = 'RECLAIM_KEY_LEVEL'
    CONFIRMED_SUPPORT_BREAK = 'CONFIRMED_SUPPORT_BREAK'
    FAILED_RECLAIM = 'FAILED_RECLAIM'
    MEDIUM_TERM_STRUCTURE_WEAKENING = 'MEDIUM_TERM_STRUCTURE_WEAKENING'
    MEDIUM_TERM_TREND_BREAK = 'MEDIUM_TERM_TREND_BREAK'
    VOLUME_CONFIRMED_BREAKOUT = 'VOLUME_CONFIRMED_BREAKOUT'
    RISK_GATES_CLEAR = 'RISK_GATES_CLEAR'
    CONSECUTIVE_CONFIRMATION = 'CONSECUTIVE_CONFIRMATION'
    DATA_READY = 'DATA_READY'
    OVEREXTENSION_COOLING = 'OVEREXTENSION_COOLING'
    TREND_CONFIRMATION = 'TREND_CONFIRMATION'


def zone_text(zone):
    return f"{zone['low']:.2f}～{zone['high']:.2f}"


def attach_triggers(d, c, config=None):
    config = config or DecisionConfig()
    T = TriggerCode
    support, resistance = c.active_support_zone, c.active_resistance_zone
    previous = c.previous_support_zone
    broken = c.previous_support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
    # Historical breaks remain risk evidence, not current reclaim targets.
    key = resistance
    def trigger(code, zone=None, **values):
        return dict(code=code, zone=zone, **values)
    hold = trigger(T.SUPPORT_HOLD, support, required_status=['HOLDING', 'RECLAIMED'])
    reclaim = trigger(T.RECLAIM_KEY_LEVEL, key, price_above=None if not key else key['high'])
    weaken = (trigger(T.BEARISH_MOMENTUM_WEAKENING, required_momentum='bearish_weakening')
              if c.short_term_direction < 0 else trigger(T.TREND_CONFIRMATION, short_term_score_min=2, medium_term_score_min=2))
    confirm = trigger(T.CONSECUTIVE_CONFIRMATION, required_observations=config.confirmation_required,
                      completed_observations=d.confirmation_count, observation_basis='completed_daily_bar')
    clear = trigger(T.RISK_GATES_CLEAR, gates=list(d.risk_gate))
    break_trigger = trigger(T.CONFIRMED_SUPPORT_BREAK, support,
        confirmed_break_atr=config.confirmed_break_atr, persistent_break_atr=config.persistent_break_atr,
        volume_ratio=config.volume_confirmation_ratio)
    d.price_context = dict(current_price=c.current_price, level_interactions=c.level_interactions, active_support_zone=support,
        active_resistance_zone=resistance, previous_support_zone=previous,
        current_active_support_status=c.current_active_support_status,
        previous_support_status=c.previous_support_status)
    if not c.data_valid:
        ready = trigger(T.DATA_READY)
        d.entry_upgrade_triggers = [ready]
        d.entry_downgrade_triggers = []
        d.holder_improve_triggers = [ready]
        d.holder_worsen_triggers = [trigger(T.MEDIUM_TERM_TREND_BREAK)]
        return d
    upgrade = []
    if d.entry_action == A.DO_NOT_CHASE:
        upgrade.append(trigger(T.OVEREXTENSION_COOLING, ma5_pct=config.overextended_ma5_pct,
                               ma20_pct=config.overextended_ma20_pct))
    if support:
        upgrade.append(hold)
    if key:
        upgrade.append(reclaim)
    if (not support and not key) or c.short_term_direction < 0:
        upgrade.append(weaken)
    if c.resistance_status in ('APPROACHING', 'TESTING') and resistance:
        upgrade.append(trigger(T.VOLUME_CONFIRMED_BREAKOUT, resistance,
            breakout_atr=config.breakout_atr, volume_ratio=config.volume_confirmation_ratio))
    if d.risk_gate:
        upgrade.append(clear)
    upgrade.append(confirm)
    if d.entry_paths:
        # Each group is an alternative. Common risk/debounce gates remain AND.
        upgrade = [dict(code='ENTRY_PATH', operator='OR', path=p['path'],
                        status=p['status'], zone=p['zone'], missing=p['missing'])
                   for p in (d.entry_paths['breakout'], d.entry_paths['pullback'])]
        if d.risk_gate:
            upgrade.append(clear)
        upgrade.append(confirm)
    d.entry_upgrade_triggers = upgrade
    d.entry_downgrade_triggers = ([break_trigger] if support else []) + [trigger(T.MEDIUM_TERM_STRUCTURE_WEAKENING, medium_term_score_max=-2)]
    d.holder_improve_triggers = ([hold] if support else []) + ([reclaim] if key else [])
    if not d.holder_improve_triggers:
        d.holder_improve_triggers.append(weaken)
    if d.risk_gate:
        d.holder_improve_triggers.append(clear)
    d.holder_improve_triggers.append(confirm)
    d.holder_worsen_triggers = ([break_trigger, trigger(T.FAILED_RECLAIM, support,
        prerequisite='CONFIRMED_SUPPORT_BREAK', subsequent_close_below=support['low'])] if support else []) + [trigger(T.MEDIUM_TERM_TREND_BREAK, medium_term_score_max=-2, short_term_score_max=-2)]
    if d.current_trigger_evidence:
        from app.decision_evidence import compact_zone
        d.current_trigger_evidence = dict(d.current_trigger_evidence,
            current_defense_zone=compact_zone(support),
            worsen_conditions=d.holder_worsen_triggers,
            improve_conditions=d.holder_improve_triggers)
    return d
