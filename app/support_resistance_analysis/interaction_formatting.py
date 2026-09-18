"""Translate structured interactions; no price classification or trading policy."""
from .interaction import LevelInteractionState as S


def interaction_text(interaction):
    if not interaction:
        return ''
    state = interaction['state']
    labels = {
        S.ABOVE_SUPPORT: '位於支撐區上方，尚未測試',
        S.BELOW_RESISTANCE: '位於壓力區下方，尚未測試',
        S.TESTING_SUPPORT: '正在測試支撐區',
        S.TESTING_RESISTANCE: '正在測試壓力區',
        S.SUPPORT_BREAKDOWN_PENDING: '已跌破支撐區下緣，等待有效失守確認',
        S.RESISTANCE_BREAKOUT_PENDING: '已突破壓力區上緣，等待有效突破確認',
        S.SUPPORT_BREAKDOWN_CONFIRMED: '支撐跌破確認',
        S.RESISTANCE_BREAKOUT_CONFIRMED: '壓力突破確認',
        S.SUPPORT_BREAKDOWN_FAILED: '支撐跌破失敗，價格重新站回支撐區' + ('上方' if interaction['location'] == 'above' else ''),
        S.RESISTANCE_BREAKOUT_FAILED: '壓力突破失敗，價格重新' + ('跌回壓力區下方' if interaction['location'] == 'below' else '回到壓力區'),
    }
    text = labels.get(state, '')
    if state in (S.TESTING_SUPPORT, S.TESTING_RESISTANCE):
        text += '，價格' + {'lower': '接近區間下緣', 'middle': '位於區間中段', 'upper': '接近區間上緣'}[interaction['position_in_zone']]
    return text


def interaction_distance_text(interaction):
    if not interaction or interaction['distance_pct'] is None:
        return ''
    role = '支撐' if interaction['role'] == 'support' else '壓力'
    crossed = interaction['state'] in (S.SUPPORT_BREAKDOWN_PENDING, S.SUPPORT_BREAKDOWN_CONFIRMED,
                                      S.RESISTANCE_BREAKOUT_PENDING, S.RESISTANCE_BREAKOUT_CONFIRMED)
    label = ('已低於支撐下緣' if role == '支撐' else '已高於壓力上緣') if crossed else f'距{role}區約'
    return f"{label} {interaction['distance_pct']:+.2f}%"


def interaction_guidance(zone):
    i = (zone or {}).get('interaction')
    if not i:
        return ''
    area = f"{i['zone_low']:.2f}～{i['zone_high']:.2f}"
    messages = {
        S.BELOW_RESISTANCE: f'後續觀察價格接近 {area} 壓力區後的反應。',
        S.TESTING_RESISTANCE: f'目前正在測試 {area} 壓力區，後續需觀察能否站穩區間上緣並取得有效突破確認。',
        S.RESISTANCE_BREAKOUT_PENDING: f'目前已突破 {area} 壓力區上緣，但尚未完成有效突破確認，需觀察是否能持續站穩；角色待確認。',
        S.RESISTANCE_BREAKOUT_CONFIRMED: f'{area} 壓力區已完成突破確認，原壓力後續可觀察是否轉為支撐。',
        S.ABOVE_SUPPORT: f'後續觀察價格接近 {area} 支撐區後能否承接。',
        S.TESTING_SUPPORT: f'目前正在測試 {area} 支撐區，需觀察能否守穩。',
        S.SUPPORT_BREAKDOWN_PENDING: f'目前已跌破 {area} 支撐區下緣，但尚未完成有效失守確認；角色待確認。',
        S.SUPPORT_BREAKDOWN_CONFIRMED: f'{area} 支撐區已確認失守，需留意下方下一層支撐與結構轉弱風險。',
    }
    return messages.get(i['state'], area + '：' + interaction_text(i) + '，需重新觀察區間反應。')
