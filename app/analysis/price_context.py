"""Enrich explanations only; consume existing zones without detection or scoring."""
from copy import deepcopy
from dataclasses import dataclass
from app.market_data import is_finite_number


@dataclass(frozen=True)
class PriceContextConfig:
    min_strength: float = 4.0
    max_support_distance_pct: float = 8.0
    near_resistance_pct: float = 3.0


def enrich_price_context(analysis, sr, config=None):
    c = config or PriceContextConfig()
    if not isinstance(sr, dict) or sr.get('error'):
        return analysis
    price = sr.get('current_price')
    if not is_finite_number(price) or price <= 0:
        return analysis
    if any(analysis[key]['label'] == '資料不足' for key in ('short_term', 'medium_term')):
        return analysis

    from app.decision_zones import displayed_zones
    selected = displayed_zones(sr)
    def select(key, max_distance=None):
        z = selected[key.removesuffix('_zones')]
        if z is None or z.get('strength_score', 0) < c.min_strength:
            return None
        if max_distance is not None and abs(z.get('distance_pct', 0)) > max_distance:
            return None
        return z

    active = select('active_zones')
    support = select('support_zones', c.max_support_distance_pct)
    resistance = select('resistance_zones', c.near_resistance_pct)
    if not any((active, support, resistance)):
        return analysis
    result = deepcopy(analysis)
    short, medium = result['short_term'], result['medium_term']
    sd = _direction(short['score'])
    md = _direction(medium['score'])
    if not active and not support and sd < 0:
        return analysis

    # Each paragraph has one purpose. Price scenarios belong only in overall.
    inside_active = bool(active) and (not active.get('interaction') or active['interaction']['location'] == 'inside')
    short['summary'] = _short_text(sd, inside_active)
    medium['summary'] = _medium_text(sd, md)
    situation = _situation_text(sd, md)
    scenario = _price_scenario(sd, md, active, support, resistance)
    result['overall_summary'] = situation + ('\n\n' + scenario if scenario else '')

    # Remove only risks already expressed by this scenario, not extra warnings.
    covered = set()
    if support or active:
        covered.add('短期正在修正，若跌破中期支撐，波段趨勢可能轉弱')
    if sd > 0 and md < 0:
        covered.add('短期與中期方向不一致，反彈尚未確認為波段反轉')
        covered.add('中期結構尚未翻多，短期正向訊號需降低解讀強度')
    elif md < 0:
        covered.add('中期結構尚未翻多，短期正向訊號需降低解讀強度')
    result['overall_warnings'] = list(dict.fromkeys(
        warning for warning in result['overall_warnings'] if warning not in covered))
    return result


def _direction(score):
    # Existing narrative direction thresholds; no score changes.
    return 1 if score >= 2 else -1 if score <= -2 else 0


def _short_text(direction, active):
    if direction < 0:
        text = '短期價格與動能整體偏弱，近期賣壓尚未完全解除。'
        return text + ('目前股價正在測試重要價格區，短線先觀察能否止跌並重新站回短期均線。'
                       if active else '短線先觀察能否止跌並重新站回短期均線，暫不宜把局部回升視為反轉。')
    if direction > 0:
        text = '短期價格與動能正在改善，多方逐漸取得主動。'
        return text + ('目前仍在重要價格區內整理，需觀察動能能否延續。'
                       if active else '短線重點在於能否維持均線支撐，並讓價格與動能持續配合。')
    return ('短期多空訊號尚未形成一致方向，價格仍在整理。'
            + ('目前正在測試重要價格區，需等待短期動能進一步表態。'
               if active else '先觀察短期均線與動能能否形成共識。'))


def _medium_text(short_direction, medium_direction):
    if medium_direction > 0:
        if short_direction < 0:
            return '中期均線與波段結構仍偏多，但近期動能轉弱，目前較接近多頭架構中的修正，尚未看到主要中期趨勢被完全破壞。'
        return '中期均線與波段結構仍偏多，主要上升架構尚在。後續需追蹤月線、季線與波段高低點能否維持原有方向。'
    if medium_direction < 0:
        if short_direction > 0:
            return '中期均線與波段結構仍偏弱，近期反彈尚不足以確認趨勢反轉。月線、季線與高低點結構是否改善，仍是波段修復的重點。'
        return '中期均線與波段結構仍偏弱，主要下降壓力尚未解除。即使短線出現止跌，也需觀察中期均線與高低點是否同步改善。'
    return '中期均線與波段高低點尚未形成一致方向，目前較接近區間整理，仍需等待月線、季線與價格結構進一步確認。'


def _situation_text(sd, md):
    if sd > 0 and md > 0:
        return '短中期結構同步偏多，近期動能與波段方向相互配合。目前重點是觀察續強能否獲得價格結構確認，並留意短線拉回風險。'
    if sd < 0 and md > 0:
        return '短期動能轉弱，但中期多頭架構仍在，目前較接近波段上升中的整理，尚不能直接視為中期反轉。'
    if sd > 0 and md < 0:
        return '短線出現反彈，但中期結構仍偏弱。這波回升目前較偏向弱勢結構中的修復，能否轉為波段反轉仍需更多確認。'
    if sd < 0 and md < 0:
        return '短中期結構同步偏弱，賣壓仍占優勢。即使出現短線回升，也需等待中期結構改善，才能提高對反轉的信心。'
    if sd == 0 and md > 0:
        return '短線方向尚未明朗，但中期多頭架構仍在，目前先以整理看待，等待動能重新表態。'
    if sd == 0 and md < 0:
        return '短線暫時整理，中期結構仍偏弱。動能尚未形成共識前，不宜把止跌直接視為波段轉強。'
    if sd > 0:
        return '短期動能改善，但中期方向仍未確認。目前需觀察反彈能否帶動波段結構，而非只看單日漲勢。'
    if sd < 0:
        return '短期動能轉弱，中期則仍在整理。後續重點是觀察修正會否擴大成波段轉弱。'
    return '目前短中期方向皆不明確，價格仍處於整理階段，需等待關鍵區域有效突破或跌破後再確認方向。'


def _price_scenario(sd, md, active, support, resistance):
    from app.support_resistance_analysis.interaction import LevelInteractionState as I
    from app.support_resistance_analysis.interaction_formatting import interaction_guidance
    interactions = [z for z in (active, resistance, support) if z and z.get('interaction')
                    and z['interaction']['state'] not in (I.ABOVE_SUPPORT, I.BELOW_RESISTANCE)]
    if interactions:
        return '\n'.join(interaction_guidance(z) for z in interactions)
    def area(zone):
        return f"{zone['low']:.2f}～{zone['high']:.2f}"
    support_name = '強支撐' if support and support.get('strength_label') in ('strong', 'very_strong') else '支撐'
    if active:
        text = f'現價正在測試 {area(active)} 的重要價格區。'
        if md < 0:
            text += '若能守穩並改善短期動能，反彈有機會延續，但仍需確認中期結構是否修復'
        elif sd < 0:
            text += '若能守穩並重新站回短期均線，短線有機會重新轉強'
        else:
            text += '若能守穩且動能續強，短線有機會延續上攻'
        if resistance:
            text += f'，上方先觀察 {area(resistance)} 壓力'
        text += '；若有效失守目前區域，'
        text += (f'則需進一步觀察 {area(support)} {support_name}是否能承接。'
                 if support else '需留意修正擴大，並追蹤中期均線與波段低點是否同步轉弱。')
        return text
    if resistance and sd >= 0:
        text = f'上方 {area(resistance)} 為近期壓力。'
        text += ('若能有效突破，反彈有機會延續，但仍需確認中期結構是否改善'
                 if md < 0 else '若能有效突破且動能配合，趨勢有機會延續')
        text += '；若突破失敗，則需留意短線拉回'
        return text + (f'，並觀察 {area(support)} {support_name}能否承接。' if support else '。')
    if support:
        return (f'下方先觀察 {area(support)} {support_name}。若能承接且短期動能改善，修正有機會緩和；'
                '若有效失守，則需留意賣壓延續，以及中期結構轉弱的風險。')
    # A distant/irrelevant upside zone should not distract from weak momentum.
    return ''
