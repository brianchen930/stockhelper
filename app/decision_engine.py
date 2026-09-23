"""Deterministic market decisions. No IO; scores are not probabilities."""
from dataclasses import dataclass, field
from enum import StrEnum
from app.position_status import PositionStatus


class ActionState(StrEnum):
    AVOID = 'AVOID'
    WAIT = 'WAIT'
    WATCH_FOR_CONFIRMATION = 'WATCH_FOR_CONFIRMATION'
    ALLOW_PROBE_ENTRY = 'ALLOW_PROBE_ENTRY'
    ENTRY_CONDITION_MET = 'ENTRY_CONDITION_MET'
    DO_NOT_CHASE = 'DO_NOT_CHASE'


class HolderActionState(StrEnum):
    HOLD = 'HOLD'
    HOLD_WITH_CAUTION = 'HOLD_WITH_CAUTION'
    TIGHTEN_RISK = 'TIGHTEN_RISK'
    REDUCE_EXPOSURE = 'REDUCE_EXPOSURE'
    EXIT_CONDITION_APPROACHING = 'EXIT_CONDITION_APPROACHING'


class ReasonCode(StrEnum):
    LOW_SUPPORT_PROBABILITY = 'LOW_SUPPORT_PROBABILITY'
    HIGH_SUPPORT_PROBABILITY = 'HIGH_SUPPORT_PROBABILITY'
    STRONG_INSTITUTIONAL_PRESSURE = 'STRONG_INSTITUTIONAL_PRESSURE'
    POSITIVE_INSTITUTIONAL_FLOW = 'POSITIVE_INSTITUTIONAL_FLOW'
    BEARISH_SHORT_TREND = 'BEARISH_SHORT_TREND'
    BULLISH_SHORT_TREND = 'BULLISH_SHORT_TREND'
    BEARISH_MACD_ACCELERATION = 'BEARISH_MACD_ACCELERATION'
    BULLISH_MACD_ACCELERATION = 'BULLISH_MACD_ACCELERATION'
    SELLING_WEAKENING = 'SELLING_WEAKENING'
    EXTREME_VOLATILITY = 'EXTREME_VOLATILITY'
    HIGH_VOLATILITY = 'HIGH_VOLATILITY'
    NEAR_SUPPORT = 'NEAR_SUPPORT'
    NEAR_RESISTANCE = 'NEAR_RESISTANCE'
    SUPPORT_BREAK = 'SUPPORT_BREAK'
    PREVIOUS_SUPPORT_BREAK = 'PREVIOUS_SUPPORT_BREAK'
    MINOR_SUPPORT_BREAK = 'MINOR_SUPPORT_BREAK'
    SUPPORT_HOLD = 'SUPPORT_HOLD'
    SUPPORT_RECLAIMED = 'SUPPORT_RECLAIMED'
    RESISTANCE_BREAKOUT = 'RESISTANCE_BREAKOUT'
    RESISTANCE_REJECTION = 'RESISTANCE_REJECTION'
    OVEREXTENDED = 'OVEREXTENDED'
    RELATIVE_WEAKNESS = 'RELATIVE_WEAKNESS'
    RELATIVE_STRENGTH = 'RELATIVE_STRENGTH'
    BOTH_TRENDS_BEARISH = 'BOTH_TRENDS_BEARISH'
    DATA_INSUFFICIENT = 'DATA_INSUFFICIENT'
    CONFIRMATION_PENDING = 'CONFIRMATION_PENDING'


@dataclass(frozen=True)
class DecisionConfig:
    confirmation_required: int = 2
    confirmed_break_atr: float = .75
    persistent_break_atr: float = .35
    breakout_atr: float = .5
    near_zone_atr: float = 1.
    volume_confirmation_ratio: float = 1.5
    overextended_ma5_pct: float = 6.
    overextended_ma20_pct: float = 12.
    entry_score_min: float = 6.
    probe_score_min: float = 3.
    min_support_strength: float = 4.
    probability_zone_overlap: float = .7
    relative_strength_threshold: float = 2.
    tighten_risk_score: float = 4.
    bearish_acceleration_atr: float = .02
    retained_evidence_max_days: int = 10
    trade_min_upside_pct: float = 2.
    trade_add_upside_pct: float = 3.
    trade_extreme_atr_pct: float = 8.
    holder_large_profit_pct: float = 10.
    holder_near_cost_pct: float = 3.
    trade_observe_score: float = 1.
    trade_consider_reduce_score: float = 3.
    trade_reduce_score: float = 6.
    trade_exit_score: float = 9.
    trade_medium_weight: float = 3.
    trade_support_weight: float = 4.
    trade_macd_weight: float = 2.5
    trade_institutional_weight: float = 3.
    trade_relative_weight: float = 2.
    trade_short_weight: float = .75
    trade_oscillator_weight: float = .25
    trade_volatility_multiplier: float = .15

    def __post_init__(self):
        import math
        for key, value in self.__dict__.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be positive and finite')
        if not isinstance(self.confirmation_required, int) or self.confirmation_required < 2:
            raise ValueError('confirmation_required must be an integer >= 2')
        if self.persistent_break_atr > self.confirmed_break_atr or self.probability_zone_overlap > 1:
            raise ValueError('Invalid break/overlap thresholds')
        if self.holder_near_cost_pct >= self.holder_large_profit_pct:
            raise ValueError('Near-cost threshold must be below large-profit threshold')
        if not (self.trade_observe_score < self.trade_consider_reduce_score < self.trade_reduce_score < self.trade_exit_score):
            raise ValueError('Trade action thresholds must be strictly increasing')


@dataclass(frozen=True)
class DecisionContext:
    symbol: str = ''
    observation_time: str | None = None
    observation_complete: bool = False
    short_term_direction: int = 0
    short_term_score: float = 0
    medium_term_direction: int = 0
    medium_term_score: float = 0
    support_probability: str = 'UNKNOWN'
    base_support_probability: float | None = None
    adjusted_support_probability: float | None = None
    adjusted_support_level: str | None = None
    support_strength: float | None = None
    resistance_strength: float | None = None
    distance_to_support: float | None = None
    distance_to_resistance: float | None = None
    institutional_level: str = 'UNKNOWN'
    institutional_score: float | None = None
    institutional_confidence: float = 0.
    institutional_selling_weakened: bool = False
    institutional_as_of: str | None = None
    institutional_freshness: str = 'FRESH'
    atr: float | None = None
    atr_percent: float | None = None
    volatility_level: str = '資料不足'
    macd_state: str = 'unknown'
    macd_momentum: str = 'data_insufficient'
    macd_histogram_change_atr: float | None = None
    rsi_state: str = 'UNKNOWN'
    kd_state: str = 'UNKNOWN'
    relative_market_strength: float | None = None
    price_above_ma5: bool | None = None
    price_above_ma20: bool | None = None
    price_above_ma60: bool | None = None
    volume_state: str = 'UNKNOWN'
    volume_ratio: float | None = None
    support_status: str = 'UNKNOWN'
    resistance_status: str = 'UNKNOWN'
    break_distance_atr: float | None = None
    overextended: bool = False
    data_valid: bool = True
    current_price: float | None = None
    active_support_zone: dict | None = None
    active_resistance_zone: dict | None = None
    previous_support_zone: dict | None = None
    previous_resistance_zone: dict | None = None
    previous_support_status: str = 'UNKNOWN'
    previous_resistance_status: str = 'UNKNOWN'
    current_active_support_status: str = 'UNKNOWN'
    previous_break_distance_atr: float | None = None
    zone_lifecycle_statuses: dict = field(default_factory=dict)
    level_interactions: list[dict] = field(default_factory=list)
    breakout_reference_zone: dict | None = None
    position_status: PositionStatus = PositionStatus.WATCHING
    average_cost: float | None = None
    shares: int | None = None
    entry_date: str | None = None
    unrealized_return: float | None = None
    volume_deteriorating: bool = False

    def __post_init__(self):
        if self.institutional_freshness != 'FRESH':
            for key, value in dict(institutional_level='UNKNOWN', institutional_score=None,
                                   institutional_confidence=0., institutional_selling_weakened=False).items():
                object.__setattr__(self, key, value)


@dataclass
class TradingDecision:
    entry_action: ActionState
    holder_action: HolderActionState
    entry_reasons: list[ReasonCode] = field(default_factory=list)
    holder_reasons: list[ReasonCode] = field(default_factory=list)
    entry_score: float = 0
    risk_score: float = 0
    risk_gate: list[ReasonCode] = field(default_factory=list)
    previous_action_state: dict = field(default_factory=dict)
    confirmation_count: int = 0
    entry_upgrade_triggers: list[dict] = field(default_factory=list)
    entry_downgrade_triggers: list[dict] = field(default_factory=list)
    holder_improve_triggers: list[dict] = field(default_factory=list)
    holder_worsen_triggers: list[dict] = field(default_factory=list)
    price_context: dict = field(default_factory=dict)
    transition_events: list[dict] = field(default_factory=list)
    transition_debug: dict = field(default_factory=dict)
    current_trigger_evidence: dict = field(default_factory=dict)
    retained_state_evidence: dict = field(default_factory=dict)
    state_basis: str = 'INSUFFICIENT_EVIDENCE'
    consistency_warnings: list[str] = field(default_factory=list)
    entry_paths: dict = field(default_factory=dict)
    decision: str = 'OBSERVE'
    position_status: str = 'WATCHING'
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    trade_evidence: dict = field(default_factory=dict)
    trade_confirmation_count: int = 0


class DecisionEngine:
    def __init__(self, config=None):
        self.config = config or DecisionConfig()

    def evaluate(self, c: DecisionContext) -> TradingDecision:
        A, H, R = ActionState, HolderActionState, ReasonCode
        reasons, gates, risk_contributions = [], [], []
        entry = risk = 0.
        def add(reason, points=0, danger=0, gate=False):
            nonlocal entry, risk
            reasons.append(reason)
            entry += points
            risk += danger
            if danger:
                risk_contributions.append({'code': str(reason), 'points': danger})
            if gate:
                gates.append(reason)
        low = c.support_probability in ('低', '極低', 'LOW', 'VERY_LOW')
        high = c.support_probability in ('高', '極高', 'HIGH', 'VERY_HIGH')
        pressure = c.institutional_level == 'STRONG_PRESSURE'
        positive = c.institutional_level in ('BULLISH', 'STRONG_SUPPORT')
        broken = c.support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
        if c.active_support_zone and c.current_price is not None:
            broken = broken and c.current_price < c.active_support_zone['low']
        previous_broken = c.previous_support_status in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
        bearish_macd = (c.macd_momentum == 'bearish_strengthening'
                       and (c.macd_histogram_change_atr is None
                            or c.macd_histogram_change_atr <= -self.config.bearish_acceleration_atr))
        if not c.data_valid:
            return self._with_triggers(TradingDecision(A.WAIT, H.HOLD_WITH_CAUTION, [R.DATA_INSUFFICIENT],
                                   [R.DATA_INSUFFICIENT], risk_gate=[R.DATA_INSUFFICIENT]), c)
        if broken:
            add(R.SUPPORT_BREAK, -3, 3, True)
        elif previous_broken:
            add(R.PREVIOUS_SUPPORT_BREAK, -3, 3, True)
        broken = broken or previous_broken  # Risk survives, but never relabels the current zone.
        if low:
            add(R.LOW_SUPPORT_PROBABILITY, -2, 1, c.support_probability in ('極低', 'VERY_LOW'))
        if pressure:
            add(R.STRONG_INSTITUTIONAL_PRESSURE, -2, 2, True)
        elif positive:
            add(R.POSITIVE_INSTITUTIONAL_FLOW, 2)
        elif c.institutional_level == 'BEARISH':
            entry -= 1
            risk += 1
            risk_contributions.append({'code': 'INSTITUTIONAL_BEARISH', 'points': 1})
        # Base rating and institutional category each contribute exactly once.
        # Adjusted probability and raw institutional score remain audit inputs only.
        if bearish_macd:
            add(R.BEARISH_MACD_ACCELERATION, -1, 1, True)
        elif c.macd_momentum == 'bullish_strengthening':
            add(R.BULLISH_MACD_ACCELERATION, 1)
        elif c.macd_momentum == 'bearish_weakening':
            add(R.SELLING_WEAKENING, 1)
        elif c.institutional_selling_weakened:
            add(R.SELLING_WEAKENING)  # Already incorporated in institutional level.
        if c.volatility_level in ('極高波動', 'EXTREME'):
            add(R.EXTREME_VOLATILITY, -1, 1, True)
        elif c.volatility_level in ('高波動', 'HIGH'):
            add(R.HIGH_VOLATILITY, -1, 1)
        if c.short_term_direction < 0:
            add(R.BEARISH_SHORT_TREND, -min(abs(c.short_term_score) / 2, 2), 1)
        elif c.short_term_direction > 0:
            add(R.BULLISH_SHORT_TREND, min(abs(c.short_term_score) / 2, 2))
        entry += max(-2, min(2, c.medium_term_score / 3))
        if c.short_term_direction < 0 and c.medium_term_direction < 0:
            add(R.BOTH_TRENDS_BEARISH, -1, 2, True)
        if high:
            add(R.HIGH_SUPPORT_PROBABILITY, 2)
        if c.overextended:
            add(R.OVEREXTENDED, -1, 1)
        if c.distance_to_resistance is not None and c.distance_to_resistance <= self.config.near_zone_atr:
            add(R.NEAR_RESISTANCE)
        if c.distance_to_support is not None and c.distance_to_support <= self.config.near_zone_atr:
            add(R.NEAR_SUPPORT)
        if c.support_status == 'MINOR_BREAK':
            add(R.MINOR_SUPPORT_BREAK, -1, 1)
        if c.support_status in ('HOLDING', 'RECLAIMED'):
            add(R.SUPPORT_HOLD if c.support_status == 'HOLDING' else R.SUPPORT_RECLAIMED, 1)
        if 'CONFIRMED_BREAKOUT' in (c.resistance_status, c.previous_resistance_status):
            add(R.RESISTANCE_BREAKOUT, 2)
        if c.resistance_status == 'REJECTED':
            add(R.RESISTANCE_REJECTION, -2, 1)
        if c.relative_market_strength is not None and abs(c.relative_market_strength) >= self.config.relative_strength_threshold:
            strong = c.relative_market_strength > 0
            add(R.RELATIVE_STRENGTH if strong else R.RELATIVE_WEAKNESS, 1 if strong else -1, 0 if strong else 1)
        # Conservative fallback; independent paths own positive entry decisions.
        if (low and pressure) or broken or R.BOTH_TRENDS_BEARISH in gates:
            action = A.AVOID
        elif c.overextended:
            action = A.DO_NOT_CHASE
        elif c.resistance_status == 'REJECTED' and (bearish_macd or c.short_term_direction < 0):
            action = A.WAIT
        elif (high and c.institutional_level in ('NEUTRAL', 'BULLISH', 'STRONG_SUPPORT')
              and (c.macd_momentum == 'bearish_weakening' or c.institutional_selling_weakened)) or c.support_status == 'RECLAIMED':
            action = A.WATCH_FOR_CONFIRMATION
        elif c.resistance_status in ('APPROACHING', 'TESTING', 'CONFIRMED_BREAKOUT') and positive and c.volume_state == 'EXPANDING':
            action = A.WATCH_FOR_CONFIRMATION
        else:
            action = A.WAIT
        if broken and c.medium_term_direction < 0 and bearish_macd and pressure:
            holder = H.EXIT_CONDITION_APPROACHING
            rule = 'BROKEN_SUPPORT_MEDIUM_BEARISH_MACD_PRESSURE'
        elif broken and (c.institutional_level in ('BEARISH', 'STRONG_PRESSURE') or bearish_macd):
            holder = H.REDUCE_EXPOSURE
            rule = 'BROKEN_SUPPORT_WITH_CONFIRMING_RISK'
        elif broken or (low and pressure) or risk >= self.config.tighten_risk_score:
            holder = H.TIGHTEN_RISK
            rule = 'SUPPORT_BREAK' if broken else 'LOW_SUPPORT_WITH_PRESSURE' if low and pressure else 'RISK_SCORE_THRESHOLD'
        elif (risk or c.short_term_direction <= 0 or c.medium_term_direction <= 0
              or c.atr is None or c.institutional_level in ('UNKNOWN', 'MIXED')
              or c.macd_momentum == 'data_insufficient'):
            holder = H.HOLD_WITH_CAUTION
            rule = 'CAUTION'
        else:
            holder = H.HOLD
            rule = 'HOLD'
        decision = TradingDecision(action, holder, list(dict.fromkeys(reasons)),
                                   list(dict.fromkeys(reasons)), round(entry, 2), risk, gates)
        from app.decision_evidence import record_evidence
        record_evidence(decision, c, rule, risk_contributions, bearish_macd, self.config)
        return self._with_triggers(decision, c)

    def _with_triggers(self, decision, context):
        from app.entry_paths import evaluate_entry_paths, entry_action
        decision.entry_paths = evaluate_entry_paths(context, decision.risk_gate, self.config)
        if (ReasonCode.RESISTANCE_BREAKOUT in decision.entry_reasons
                and decision.entry_paths['breakout']['interaction_state'] != 'RESISTANCE_BREAKOUT_CONFIRMED'):
            decision.entry_reasons.remove(ReasonCode.RESISTANCE_BREAKOUT)
            decision.entry_score = round(decision.entry_score - 2, 2)
        decision.entry_action = entry_action(decision.entry_paths, decision.entry_action, context.volatility_level)
        from app.decision_transitions import attach_triggers
        return self.evaluate_trade(attach_triggers(decision, context, self.config), context)

    def evaluate_trade(self, d, c):
        """Weight existing analysis states; never recalculate indicators.

        Positive points mean risk, negative points mean protection. Raw market
        scores/gates belong to the existing entry/holder analysis and are not
        summed again. Cost cannot change these weights or the action band.
        """
        from app.position_status import read_position_status
        cfg = self.config
        holding = read_position_status(c.position_status) == PositionStatus.HOLDING
        d.position_status = str(read_position_status(c.position_status))
        d.decision = 'OBSERVE'
        d.reasons, d.warnings, d.follow_up = [], [], []
        d.trade_confirmation_count = 0
        valid = bool(c.data_valid and c.observation_complete and c.atr and c.atr > 0
                     and c.volatility_level not in ('資料不足', 'UNKNOWN')
                     and d.state_basis != 'STALE_DATA')
        contributions = []
        def add(code, dimension, points, text):
            contributions.append(dict(code=code, dimension=dimension, points=round(points, 4), text=text))

        # Use gates that already validated current vs previous support provenance.
        broken = ReasonCode.SUPPORT_BREAK in d.risk_gate or ReasonCode.PREVIOUS_SUPPORT_BREAK in d.risk_gate
        accelerating = ReasonCode.BEARISH_MACD_ACCELERATION in d.risk_gate
        sz = c.active_support_zone
        important = c.support_strength is not None and c.support_strength >= cfg.min_support_strength
        if broken:
            previous = ReasonCode.SUPPORT_BREAK not in d.risk_gate
            zone = c.previous_support_zone if previous else sz
            strength = (zone or {}).get('strength_score') if previous else c.support_strength
            weight = cfg.trade_support_weight * (1.25 if strength is not None and strength >= cfg.min_support_strength else 1.)
            add('PREVIOUS_SUPPORT_BREAK' if previous else 'SUPPORT_BREAK', 'structure', weight,
                '前支撐已確認失守' if previous else '主要支撐已確認失守')
        elif c.support_status == 'MINOR_BREAK':
            add('MINOR_SUPPORT_BREAK', 'structure', cfg.trade_support_weight * .375, '支撐初步跌破，尚未確認失守')
        elif (sz and c.current_price is not None and c.current_price >= sz['low']
              and c.support_status in ('HOLDING', 'RECLAIMED')):
            add('SUPPORT_INTACT', 'structure', -cfg.trade_support_weight * (.75 if important else .5),
                f"支撐 {sz['low']:g}～{sz['high']:g}" + ('已收復' if c.support_status == 'RECLAIMED' else '仍守穩'))
        # Probability only adjusts an intact/unconfirmed zone, never cancels a break.
        if not broken and c.support_status != 'MINOR_BREAK' and sz:
            if c.support_probability in ('低', '極低', 'LOW', 'VERY_LOW'):
                add('LOW_SUPPORT_PROBABILITY', 'structure', .5, '支撐成功率評等偏低')
        if c.resistance_status == 'REJECTED':
            add('RESISTANCE_REJECTION', 'structure', 1., '上方壓力測試受阻')
        elif ReasonCode.RESISTANCE_BREAKOUT in d.entry_reasons:
            add('RESISTANCE_BREAKOUT', 'structure', -1., '壓力突破已確認')
        elif c.distance_to_resistance is not None and 0 <= c.distance_to_resistance <= cfg.near_zone_atr:
            add('NEAR_RESISTANCE', 'structure', .5, '現價接近上方壓力，操作空間受限')
        if c.medium_term_direction:
            add('MEDIUM_TREND', 'trend', -cfg.trade_medium_weight * c.medium_term_direction,
                '中期趨勢偏多' if c.medium_term_direction > 0 else '中期趨勢偏空')
        if c.short_term_direction:
            add('SHORT_TREND', 'trend', -cfg.trade_short_weight * c.short_term_direction,
                '短期趨勢偏多' if c.short_term_direction > 0 else '短期趨勢偏空')
        if accelerating:
            add('MACD_ACCELERATION', 'momentum', cfg.trade_macd_weight, 'MACD 空方動能加速')
        elif c.macd_momentum in ('bearish_strengthening', 'bullish_weakening'):
            add('MACD_WEAKENING', 'momentum', cfg.trade_macd_weight * .5, 'MACD 動能轉弱')
        elif c.macd_momentum in ('bearish_weakening', 'bullish_strengthening'):
            add('MACD_IMPROVING', 'momentum', -cfg.trade_macd_weight * .3,
                'MACD 空方動能減弱' if c.macd_momentum == 'bearish_weakening' else 'MACD 多方動能增強')
        if c.kd_state in ('BULLISH', 'BEARISH'):
            add('KD', 'momentum', cfg.trade_oscillator_weight * (-1 if c.kd_state == 'BULLISH' else 1),
                'KD 尚未明顯轉空' if c.kd_state == 'BULLISH' else 'KD 偏空')
        if c.rsi_state in ('OVERBOUGHT', 'OVERSOLD'):
            add('RSI', 'momentum', cfg.trade_oscillator_weight,
                'RSI 超買，短線回檔風險增加' if c.rsi_state == 'OVERBOUGHT' else 'RSI 超賣，弱勢風險仍在')
        if c.overextended:
            add('OVEREXTENDED', 'momentum', .5, '價格相對均線過度延伸')
        # The category already aggregates institutions; do not add raw score or
        # adjusted support probability again, or claim unobserved synchronous sales.
        flow_weights = {'STRONG_PRESSURE': (1., '法人賣壓明顯'), 'BEARISH': (2/3, '法人籌碼偏空'),
                        'BULLISH': (-.5, '法人籌碼偏多'), 'STRONG_SUPPORT': (-1., '法人買盤強勁')}
        if c.institutional_level in flow_weights:
            weight, label = flow_weights[c.institutional_level]
            add('INSTITUTIONAL_FLOW', 'institutional', cfg.trade_institutional_weight * weight, label)
        if c.relative_market_strength is not None and abs(c.relative_market_strength) >= cfg.relative_strength_threshold:
            weak = c.relative_market_strength < 0
            add('RELATIVE_MARKET', 'relative_market', cfg.trade_relative_weight * (1 if weak else -1),
                f"相對大盤{'落後' if weak else '領先'} {abs(c.relative_market_strength):g} 個百分點")
        if c.volume_deteriorating:
            add('VOLUME_DETERIORATING', 'volume', 1., '下跌伴隨放量，量價結構轉弱')

        extreme = c.volatility_level in ('極高波動', 'EXTREME') or (c.atr_percent is not None and c.atr_percent >= cfg.trade_extreme_atr_pct)
        high_volatility = extreme or c.volatility_level in ('高波動', 'HIGH')
        # Count NET adverse dimensions; correlated RSI/KD/MACD and short/medium
        # trends cannot masquerade as multiple independent confirmations.
        dimensions = {}
        for item in contributions:
            dimensions[item['dimension']] = dimensions.get(item['dimension'], 0.) + item['points']
        adverse = [key for key, points in dimensions.items() if points > 0]
        raw_score = sum(item['points'] for item in contributions)
        if high_volatility:
            bonus = max(0., raw_score) * cfg.trade_volatility_multiplier * (2 if extreme else 1)
            if bonus:
                add('ATR_RISK_AMPLIFIER', 'volatility', bonus, 'ATR 波動度極高，放大既有風險' if extreme else 'ATR 波動度偏高，放大既有風險')
            d.warnings.append('ATR 波動度極高，應縮小曝險並留意正常價格擺動' if extreme else 'ATR 波動度偏高，應控制持倉曝險')
        score = round(sum(item['points'] for item in contributions), 4)
        if score >= cfg.trade_exit_score and len(adverse) >= 3:
            market_action = 'EXIT'
        elif score >= cfg.trade_reduce_score and len(adverse) >= 2:
            market_action = 'REDUCE'
        elif score >= cfg.trade_consider_reduce_score and len(adverse) >= 2:
            market_action = 'CONSIDER_REDUCE'
        elif score >= cfg.trade_observe_score or not any(item['points'] < 0 for item in contributions):
            market_action = 'OBSERVE'
        else:
            market_action = 'HOLD'
        # Missing analysis is not bullish evidence. It prevents an unqualified
        # HOLD, but does not suppress established multi-signal market risk.
        missing = []
        if not sz and not broken:
            missing.append('缺少有效支撐參考')
        if c.institutional_level == 'UNKNOWN':
            missing.append('缺少有效法人資料')
        if c.macd_momentum in ('unknown', 'data_insufficient'):
            missing.append('MACD 資料不足')
        if missing and market_action == 'HOLD':
            market_action = 'OBSERVE'
        if valid:
            d.decision = market_action if holding else 'OBSERVE'
        cost_context = self._holding_cost_context(c) if holding else None
        d.trade_evidence = dict(version=4, checks={'valid': valid}, contributions=contributions,
            dimension_scores={key: round(value, 4) for key, value in dimensions.items()},
            weighted_score=score, raw_weighted_score=round(raw_score, 4), risk_dimensions=adverse,
            market_action=market_action if valid else 'OBSERVE',
            reduce_signal=market_action in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT') and valid,
            risk_evidence=[x['text'] for x in contributions if x['points'] > 0],
            positive_evidence=[x['text'] for x in contributions if x['points'] < 0],
            average_cost=c.average_cost if holding else None, cost_context=cost_context,
            unrealized_return=cost_context['return_percent'] if cost_context else None,
            thresholds=dict(observe=cfg.trade_observe_score, consider_reduce=cfg.trade_consider_reduce_score,
                            reduce=cfg.trade_reduce_score, exit=cfg.trade_exit_score))
        # Show at most four influential observations, with a meaningful opposing
        # signal when present. All contributions remain available for auditing.
        ranked = sorted(contributions, key=lambda x: abs(x['points']), reverse=True)
        primary = [x for x in ranked if (x['points'] > 0) == (market_action != 'HOLD')]
        opposing = [x for x in ranked if x not in primary]
        selected = primary[:3] + opposing[:1] if primary else opposing[:4]
        selected = sorted(selected, key=lambda x: abs(x['points']), reverse=True)
        if not valid:
            d.reasons = ['收盤或必要風險資料無效，本輪無法確認交易動作']
            d.warnings.append('資料不足或時間過舊，不能視為市場風險解除')
            selected = []
        else:
            d.reasons = [x['text'] for x in selected]
            if missing:
                d.warnings.append('；'.join(missing))
                if market_action == 'OBSERVE':
                    selected = selected[:3]
                    d.reasons = (d.reasons[:3] + ['；'.join(missing)])
            if not d.reasons:
                d.reasons = ['本輪沒有足夠方向性證據，維持觀察']
        d.trade_evidence['key_signal_codes'] = [x['code'] for x in selected]
        d.follow_up = ([f"觀察支撐 {sz['low']:g}～{sz['high']:g} 的守穩／失守與中期趨勢是否同步變化"]
                       if sz else ['待有效支撐與趨勢資料補齊後重新評估'])
        if holding:
            d.warnings.insert(0, '成本不是技術支撐；不因虧損而攤平或等待回本而延後處理市場風險')
        if cost_context and valid:
            rate = cost_context['return_percent']
            management = [f'平均成本 {c.average_cost:g}，未實現報酬 {rate:+.2f}%']
            if cost_context['state'] == 'LARGE_PROFIT':
                management.append('已有較大獲利，依有效支撐移動停利')
                if market_action in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT'):
                    management.append('市場風險已成立，可更積極執行保護獲利')
            elif rate < 0:
                management.append('不以等待回本延後減碼或退出')
            d.trade_evidence['position_management'] = management
            d.warnings[0] = '；'.join(management + [d.warnings[0]])
        return d

    def _holding_cost_context(self, c):
        """Return percentages use percentage points, matching the context adapter."""
        import math
        def finite(value):
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not all(finite(v) and v > 0 for v in (c.average_cost, c.current_price)):
            return None
        calculated = (c.current_price / c.average_cost - 1) * 100
        # Prefer the supplied P&L when consistent (including display rounding).
        rate = (c.unrealized_return if finite(c.unrealized_return)
                and abs(c.unrealized_return - calculated) <= .01 else calculated)
        if abs(rate) <= self.config.holder_near_cost_pct + 1e-8:
            state = 'NEAR_COST'
        elif rate >= self.config.holder_large_profit_pct - 1e-8:
            state = 'LARGE_PROFIT'
        else:
            state = 'LOSS' if rate < 0 else 'PROFIT'
        sz = c.active_support_zone
        distance = max(sz['low'] - c.average_cost, c.average_cost - sz['high'], 0) if sz else None
        return dict(state=state, return_percent=rate,
                    support_near_cost=distance is not None and distance / c.average_cost * 100 <= self.config.holder_near_cost_pct)
