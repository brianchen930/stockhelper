LABELS = {'weak': '弱', 'medium': '中', 'strong': '強', 'very_strong': '很強'}
METHODS = {'swing_low': 'Swing Low', 'swing_high': 'Swing High',
           'volume_profile_poc': 'Volume Profile POC', 'volume_profile_hvn': 'Volume Profile HVN',
           'rolling_vwap': 'Rolling VWAP（日線）', 'anchored_vwap_swing_low': 'Anchored VWAP（Swing Low）',
           'anchored_vwap_swing_high': 'Anchored VWAP（Swing High）', 'kmeans': 'K-Means'}


def select_active_zone(result):
    """Select one active zone for display without changing the analysis list."""
    return min((result or {}).get('active_zones', []),
               key=lambda z: (abs(z.get('distance_pct', 0)), -z.get('strength_score', 0)), default=None)


def format_support_resistance_output(result, *, research_mode=False, debug=False):
    result = result or {}
    lines = ['【支撐 / 壓力】']
    active = select_active_zone(result)
    if active:
        lines.extend([f"・目前測試區：{active['low']:.2f}～{active['high']:.2f}",
                      format_zone_strength(active), ''])
    for key, title in [('support_zones', '最近支撐'), ('resistance_zones', '最近壓力')]:
        zone = min(result.get(key, []), key=lambda z: abs(z.get('distance_pct', 0)), default=None)
        if zone:
            lines.extend([f"・{title}：{zone['low']:.2f}～{zone['high']:.2f}（{zone['distance_pct']:+.2f}%）",
                          format_zone_strength(zone), ''])
    if not any(result.get(key) for key in ('support_zones', 'resistance_zones', 'active_zones')):
        lines.append('・目前沒有偵測到足夠可靠的支撐／壓力區域。')
    if result.get('error'):
        lines.append('資料提示：支撐／壓力分析暫時無法使用。')
    from app.bayesian_support.presentation import append_probability_output
    text = append_probability_output('\n'.join(lines).rstrip(), result, research_mode=research_mode or debug)
    from app.institutional_flow.presentation import format_context
    context = format_context(result.get('institutional_context'), debug=research_mode or debug)
    return text + ('\n\n' + context if context else '')


def format_zone_strength(zone):
    label = LABELS.get(zone.get('strength_label'), zone.get('strength_label', '未知'))
    methods = '、'.join(METHODS.get(m, m) for m in zone.get('methods', []))
    return f'強度：{label}｜{methods}'
