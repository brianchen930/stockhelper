LABELS = {'weak': '弱', 'medium': '中', 'strong': '強', 'very_strong': '很強'}
METHODS = {'swing_low': 'Swing Low', 'swing_high': 'Swing High',
           'volume_profile_poc': 'Volume Profile POC', 'volume_profile_hvn': 'Volume Profile HVN',
           'rolling_vwap': 'Rolling VWAP（日線）', 'anchored_vwap_swing_low': 'Anchored VWAP（Swing Low）',
           'anchored_vwap_swing_high': 'Anchored VWAP（Swing High）', 'kmeans': 'K-Means'}

from .interaction_formatting import interaction_text, interaction_distance_text


def format_position(zone):
    interaction = zone.get('interaction')
    if not interaction:
        return []
    lines = ['目前位置：' + interaction_text(interaction)]
    distance = interaction_distance_text(interaction)
    if distance:
        lines.append(distance)
    return lines


def select_active_zone(result):
    """Select one active zone for display without changing the analysis list."""
    return min((result or {}).get('active_zones', []),
               key=lambda z: (abs(z.get('distance_pct', 0)), -z.get('strength_score', 0)), default=None)


def format_support_resistance_output(result, *, research_mode=False, debug=False):
    result = result or {}
    lines = ['【支撐 / 壓力】']
    from .selection import select_zone_display
    selected = select_zone_display(result)
    for role, title in [('support', '最近支撐'), ('resistance', '最近壓力')]:
        item = selected[role]
        if item:
            zone = item['zone']
            lines.extend([f"・{title}：{zone['low']:.2f}～{zone['high']:.2f}",
                          *format_position(zone), format_zone_strength(zone), ''])
    secondary = selected['secondary']
    if secondary:
        zone = secondary['zone']
        state = (zone.get('interaction') or {}).get('state', '')
        confirmed = state.endswith('_CONFIRMED')
        resistance = state.startswith('RESISTANCE_')
        description = ('原壓力已突破，若後續回踩守穩，才可能確認轉為支撐。' if resistance else
                       '原支撐已跌破，若後續反彈受阻，才可能確認轉為壓力。') if confirmed else (
                       '尚待有效突破確認，目前不是正式支撐。' if resistance else
                       '尚待有效失守確認，目前不是正式壓力。')
        lines.extend([f"・次要觀察區：{zone['low']:.2f}～{zone['high']:.2f}", description])
    lifecycle = result.get('zone_lifecycle')
    if not selected['support'] and not selected['resistance']:
        lines.append('・目前沒有足夠可靠的有效支撐／壓力區域。')
    if result.get('error'):
        lines.append('資料提示：支撐／壓力分析暫時無法使用。')
    from app.bayesian_support.presentation import append_probability_output
    text = append_probability_output('\n'.join(lines).rstrip(), result, research_mode=research_mode or debug)
    from app.institutional_flow.presentation import format_context
    context = format_context(result.get('institutional_context'), debug=research_mode or debug)
    text += ('\n\n' + context if context else '')
    if lifecycle and (debug or research_mode):
        import json
        text += '\n\n[ZONE DEBUG]\n' + json.dumps(dict(zones=lifecycle.get('records', lifecycle),
            bayesian_events=lifecycle.get('bayesian_events', [])), ensure_ascii=False, indent=2, allow_nan=False)
    return text


def format_zone_strength(zone):
    label = LABELS.get(zone.get('strength_label'), zone.get('strength_label', '未知'))
    methods = '、'.join(METHODS.get(m, m) for m in zone.get('methods', []))
    if zone.get('status') == 'SUPPORT_TO_RESISTANCE':
        return f'強度：{label}｜原支撐轉壓力｜來源：{methods}'
    if zone.get('status') == 'RESISTANCE_TO_SUPPORT':
        return f'強度：{label}｜原壓力轉支撐｜來源：{methods}'
    return f'強度：{label}｜{methods}'
