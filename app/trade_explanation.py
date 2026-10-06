"""Explain finalized actions using recorded contributions and transition paths.

No indicator calculation, score changes, or new trading prerequisites live here.
Entry paths remain alternatives; a blocked breakout cannot block a ready pullback.
"""
from app.decision_transitions import zone_text


GATE_REPAIRS = {
    'SUPPORT_BREAK': '主要支撐失守風險解除',
    'PREVIOUS_SUPPORT_BREAK': '前支撐失守風險解除',
    'LOW_SUPPORT_PROBABILITY': '支撐成功率評等改善',
    'STRONG_INSTITUTIONAL_PRESSURE': '法人明顯賣壓緩和',
    'BEARISH_MACD_ACCELERATION': 'MACD 空方動能不再加速',
    'BOTH_TRENDS_BEARISH': '短中期趨勢不再同步偏空',
    'BEARISH_MEDIUM_TREND': '中期趨勢恢復偏多',
    'DATA_INSUFFICIENT': '必要行情與風險資料補齊',
    'EXTREME_VOLATILITY': '極高波動限制解除',
    'HIGH_VOLATILITY': '高波動的回檔路徑限制解除',
    'OVEREXTENDED': '價格正乖離回到進場允許範圍',
    'INSTITUTIONAL_DATA_UNCERTAIN': '法人資料恢復有效且方向不再分歧',
}
GATE_LIMITS = {
    'SUPPORT_BREAK': '主要支撐已確認失守',
    'PREVIOUS_SUPPORT_BREAK': '前支撐已確認失守',
    'LOW_SUPPORT_PROBABILITY': '支撐成功率評等偏低',
    'STRONG_INSTITUTIONAL_PRESSURE': '法人賣壓明顯',
    'BEARISH_MACD_ACCELERATION': 'MACD 空方動能加速',
    'BOTH_TRENDS_BEARISH': '短中期趨勢同步偏空',
    'BEARISH_MEDIUM_TREND': '中期趨勢偏空',
    'DATA_INSUFFICIENT': '必要行情或風險資料不足',
    'EXTREME_VOLATILITY': '極高波動限制此路徑進場',
    'HIGH_VOLATILITY': '高波動限制回檔型進場',
    'OVEREXTENDED': '價格正乖離過大，尚不符合進場條件',
    'INSTITUTIONAL_DATA_UNCERTAIN': '法人資料不足或方向分歧，尚不符合此路徑條件',
}
GATE_CONTRIBUTIONS = {'BEARISH_MACD_ACCELERATION': 'MACD_ACCELERATION',
                      'STRONG_INSTITUTIONAL_PRESSURE': 'INSTITUTIONAL_FLOW',
                      'BOTH_TRENDS_BEARISH': 'MEDIUM_TREND'}
RISK_REPAIRS = {
    'MEDIUM_TREND': '中期趨勢不再偏空',
    'MACD_ACCELERATION': 'MACD 空方動能不再加速',
    'MACD_WEAKENING': 'MACD 動能改善',
    'INSTITUTIONAL_FLOW': '法人賣壓緩和',
    'RELATIVE_MARKET': '相對大盤落後幅度收斂',
    'VOLUME_DETERIORATING': '下跌放量情況緩和',
    'OVEREXTENDED': '價格正乖離回到進場允許範圍',
}


def key_factors(items):
    """Prefer material evidence and avoid repeating correlated minor indicators."""
    major = [item for item in items if item['code'] not in ('KD', 'RSI', 'SHORT_TREND')]
    selected, dimensions = [], set()
    for item in major or items:
        dimension = item['dimension']
        if dimension not in dimensions:
            selected.append(item)
            dimensions.add(dimension)
    return selected


def factor_text(item):
    return 'KD 尚未明顯轉空' if item['code'] == 'KD' and item['points'] < 0 else item['text']


def entry_blocking_factors(path):
    """Only unmet conditions, capped at three; soft risks are not repairs."""
    if path.get('ready'):
        return []
    factors = [GATE_REPAIRS[code] for code in path.get('risk_blockers', []) if code in GATE_REPAIRS]
    pending = []
    for text in path.get('missing', []):
        if '收盤' in text:
            pending.append(text)
        else:
            factors.append(text.replace('趨勢尚未符合此路徑條件', '中期趨勢尚未偏多'))
    # A close is the next obstacle only once the setup and hard gates allow it.
    if not factors and (pending or path.get('core_met')):
        required = path.get('confirmation_required', 1)
        count = path.get('confirmation_count', 0)
        basis = '、'.join(path.get('confirmation_reasons', [])[:2])
        pending_text = (f'等待連續 {required} 根新收盤日線確認（目前 {count}/{required}）'
                        if required > 1 else f'等待本根日線收盤確認（目前 {count}/{required}）')
        factors.append((basis + '，' if basis else '') + pending_text)
    return list(dict.fromkeys(factors))[:3]


def entry_conditions(path):
    """Report actual obstacles separately, without inventing an AND checklist."""
    zone = path.get('zone')
    if not zone or path.get('status') in ('INACTIVE', 'INVALIDATED'):
        return []
    name = '突破型' if path.get('path') == 'BREAKOUT_ENTRY' else '回檔型'
    if path.get('ready'):
        return [zone_text(zone) + ' ' + name + '條件維持成立']
    return [zone_text(zone) + ' ' + name + '：' + factor for factor in entry_blocking_factors(path)]


def explain_trade(decision):
    evidence = decision.get('trade_evidence') or {}
    checks = evidence.get('checks') or {}
    valid = checks.get('valid')
    if valid is not True:
        reasons = list(checks.get('blocking_reasons') or decision.get('reasons') or [])
        follow = []
        for text in decision.get('follow_up') or []:
            if '→' in text:
                follow.append(text)
            elif valid is False:
                # Engine supplies concrete data/close repairs for invalid rounds.
                condition = text.removeprefix('待').replace('後重新評估風險', '').replace('後重新評估', '')
                follow.append(condition + ' → 重新評估交易動作')
        return reasons, [], follow

    action = decision['decision']
    holding = decision.get('position_status') == 'HOLDING'
    holding_action = action in ('HOLD', 'ADD')
    reducing = action in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT')
    ranked = sorted((x for x in evidence.get('contributions', [])
                     if x['points'] and x.get('dimension') != 'volatility'),
                    key=lambda x: abs(x['points']), reverse=True)
    risks = key_factors([x for x in ranked if x['points'] > 0])
    protection = key_factors([x for x in ranked if x['points'] < 0])
    codes = {x['code'] for x in ranked}
    constraints = list(evidence.get('action_constraints') or [])
    recovery = evidence.get('recovery') or {}
    paths = decision.get('entry_paths') or {}
    ready = next((paths[k] for k in ('breakout', 'pullback') if paths.get(k, {}).get('ready')), None)
    path = ready or paths.get(paths.get('primary_path')) or {}
    path_missing = []
    path_limits = []
    if not ready and path.get('zone') and path.get('status') not in ('INACTIVE', 'INVALIDATED'):
        name = '突破型' if path.get('path') == 'BREAKOUT_ENTRY' else '回檔型'
        path_missing = [name + '：' + text.replace('支撐強度尚未符合既有門檻', '支撐強度尚不足以形成回檔候選')
                        for text in path.get('missing', [])]
        path_limits = [name + '：' + GATE_LIMITS[code] for code in path.get('risk_blockers', [])
                       if code in GATE_LIMITS]

    primary_factors = protection if holding_action else risks
    primary = [factor_text(x) for x in primary_factors]
    primary_codes = {x['code'] for x in primary_factors[:4]}
    # The retained action is supported by incomplete recovery, not a HOLD candidate.
    if reducing and recovery.get('pending'):
        primary.insert(0, '先前減碼警戒仍保留，本輪改善尚未完成連續收盤確認')
    elif constraints:
        primary = constraints + primary
    elif not holding and not risks:
        if path_missing:
            primary = path_missing[:1]
        elif path_limits:
            primary = path_limits[:1]
        elif ready:
            name = '突破型' if ready.get('path') == 'BREAKOUT_ENTRY' else '回檔型'
            primary = [name + '條件已成立，列為進場評估候選']
    # No generic explanation is invented when a payload contains no evidence.
    effect = evidence.get('volatility_effect') or {}
    if effect.get('affects_action') and action == effect.get('action_with_volatility'):
        primary = primary[:3] + [x['text'] for x in evidence.get('contributions', [])
                                 if x['code'] == 'ATR_RISK_AMPLIFIER']
    if decision.get('bypass_persistence'):
        primary.insert(0, '重大風險已觸發，略過連續確認，最終訊號更新為' + decision['final_action_state'])
    primary = primary[:4]

    limits = []
    if checks.get('observation_complete') is False:
        limits.append('盤中評估；進場確認與風險解除仍以已收盤日線為準')
    if not reducing:
        modifiers = path.get('risk_modifiers', [])
        labels = {'EXTREME_VOLATILITY': 'ATR 極高', 'HIGH_VOLATILITY': '高波動',
                  'OVEREXTENDED': '價格正乖離過大'}
        if modifiers:
            limits.append('、'.join(labels[code] for code in modifiers) + '，進場上限為小幅試單')
    if holding_action:
        limits.extend(factor_text(x) + '，暫不提高曝險' for x in risks[:2])
    elif holding and action != 'EXIT':
        suffix = '，暫不擴大減碼' if reducing else '，暫不直接減碼'
        limits.extend(factor_text(x) + suffix for x in protection[:2])
    # A ready alternative removes path restrictions; an EXIT does not advertise
    # minor protective indicators as a reason to postpone the recorded action.
    if not reducing:
        # Do not restate the same MACD or gate under another path label.
        if holding_action and any(x['code'] in ('MACD_ACCELERATION', 'MACD_WEAKENING') for x in risks[:2]):
            path_missing = [text for text in path_missing if '等待短期動能改善' not in text]
        path_limits = [text for text in path_limits if not any(
            GATE_LIMITS[code] in text and GATE_CONTRIBUTIONS.get(code, code) in primary_codes
            for code in path.get('risk_blockers', []) if code in GATE_LIMITS)]
        limits.extend(text for text in path_missing + path_limits
                      if text not in primary and not any(text.split('：', 1)[-1] in item for item in primary))
    limits = list(dict.fromkeys(limits))[:3]

    context = decision.get('price_context') or {}
    structured = context.get('support_policy') == 'STRUCTURAL_V1'
    support = context.get('structural_support_zone') if structured else context.get('active_support_zone')
    support_label = '結構防守' if structured else '支撐'
    short_zone = context.get('short_term_support_zone')
    short_broken = evidence.get('short_term_support_broken', False)
    worsen = {x['code']: x for x in decision.get('holder_worsen_triggers') or []}
    improve = {x['code']: x for x in decision.get('holder_improve_triggers') or []}
    broken = 'SUPPORT_BREAK' in codes or context.get('structural_support_status' if structured else 'current_active_support_status') in (
        'CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
    follow = []
    if holding_action and support and not broken and 'SUPPORT_INTACT' in codes:
        follow.append(zone_text(support) + ' ' + support_label + '持續守穩 → 維持持有')
    if holding and short_zone:
        follow.append(zone_text(short_zone) + (' 短線支撐失守 → 短線轉弱，停止加碼／觀察是否站回'
                      if short_broken else ' 短線支撐若失守 → 停止加碼、提高警戒／觀察是否站回'))
    if recovery.get('pending'):
        target = {'HOLD': '恢復持有', 'OBSERVE': '轉為觀察',
                  'CONSIDER_REDUCE': '降為考慮減碼', 'REDUCE': '降低退出警戒'}.get(recovery.get('candidate'), '降低減碼警戒')
        basis = '、'.join(factor_text(x) for x in protection[:2]) or '本輪改善條件'
        follow.append(f"{basis}，連續 {recovery['required']} 根新收盤日線成立"
                      f"（目前 {recovery['count']}/{recovery['required']}） → 評估{target}")
    elif holding and not holding_action:
        repairs = []
        hold = improve.get('SUPPORT_HOLD', {}).get('zone')
        reclaim = improve.get('RECLAIM_KEY_LEVEL', {}).get('zone')
        if hold:
            repairs.append(zone_text(hold) + ' ' + support_label + '確認守穩')
        elif reclaim:
            repairs.append('重新站上 ' + zone_text(reclaim) + ' 並確認')
        elif 'BEARISH_MOMENTUM_WEAKENING' in improve:
            repairs.append('短期空方動能減弱')
        elif 'TREND_CONFIRMATION' in improve:
            repairs.append('短中期趨勢恢復偏多')
        if repairs:
            repairs.extend(GATE_REPAIRS[code] for code in improve.get('RISK_GATES_CLEAR', {}).get('gates', [])
                           if code in GATE_REPAIRS)
            repairs.extend(RISK_REPAIRS[item['code']] for item in risks if item['code'] in RISK_REPAIRS)
            if constraints:
                repairs.extend({'缺少有效支撐參考': '有效支撐參考形成',
                    '缺少有效結構防守參考': '有效結構防守參考形成',
                    '缺少有效法人資料': '有效法人資料補齊', 'MACD 資料不足': 'MACD 資料補齊'}[reason]
                    for reason in constraints if reason in ('缺少有效支撐參考', '缺少有效結構防守參考', '缺少有效法人資料', 'MACD 資料不足'))
            confirm = improve.get('CONSECUTIVE_CONFIRMATION')
            if confirm:
                repairs.append(f"連續 {confirm['required_observations']} 根新收盤日線確認")
            target = '重新評估退出條件' if action == 'EXIT' else '評估降低減碼警戒' if reducing else '評估恢復持有'
            follow.append('，'.join(dict.fromkeys(repairs)) + ' → ' + target)
    if holding and action != 'EXIT' and 'MEDIUM_TERM_TREND_BREAK' in worsen:
        target = '評估退出' if action == 'REDUCE' else '評估進一步減碼' if reducing else '評估減碼'
        if support and 'CONFIRMED_SUPPORT_BREAK' in worsen:
            condition = (zone_text(support) + ' ' + support_label + '失守後未能站回' if broken
                         else zone_text(support) + ' ' + support_label + '確認跌破')
            follow.append(condition + '且中期趨勢轉弱 → ' + target)
        elif not structured and 'MEDIUM_TREND' in codes:
            follow.append('短中期趨勢同步轉弱 → ' + target)
    if not reducing and not (holding and short_broken):
        conditions = entry_conditions(path)
        if conditions:
            target = '評估加碼' if holding_action else '評估進場' if not holding else '重新評估持有與加碼條件'
            follow.extend(condition + ' → ' + target for condition in conditions)
    return primary, limits, list(dict.fromkeys(follow))
