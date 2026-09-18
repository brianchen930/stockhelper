"""Short, deterministic explanations driven by decision states and reasons."""
from app.decision_engine import ActionState as A, HolderActionState as H, ReasonCode as R

ENTRY_LABELS = dict(zip(A, ('暫不介入', '觀望', '等待確認', '可小幅試單', '進場條件成立', '不宜追價')))
HOLDER_LABELS = dict(zip(H, ('續抱', '謹慎續抱', '提高風險警戒', '降低曝險', '退出條件接近')))


def format_entry_paths(paths):
    """Render evaluator results only; no indicator, risk or price evaluation."""
    from app.decision_transitions import zone_text
    from app.entry_paths import PathStatus as S
    def clause(key, primary):
        p = paths[key]
        name = '突破型' if key == 'breakout' else '回檔型'
        area = zone_text(p['zone']) if p['zone'] else ''
        prefix = ('主要進場路徑：' if primary else '另一條獨立路徑：') + name + '；'
        if p['status'] == S.INVALIDATED:
            detail = ('先前壓力突破失敗，突破型進場條件尚未成立' if key == 'breakout'
                      else p['missing'][0])
        elif p['status'] == S.READY:
            detail = area + '進場條件成立，可列為進場候選'
        elif p['status'] == S.BLOCKED_BY_RISK:
            detail = area + (' 壓力區已完成突破確認' if key == 'breakout' else '支撐價位條件已具備') + '，但風險限制尚未解除，暫不形成進場候選'
        elif p['status'] == S.INACTIVE and key == 'breakout' and not area:
            detail = '目前缺少有效壓力參考，暫無突破型進場候選'
        elif p['status'] in (S.INACTIVE, S.WATCHING) and key == 'pullback':
            detail = ('若後續拉回，觀察 ' + area + ' 支撐；須守穩確認、短期動能改善且風險允許，才具備回檔型進場確認條件'
                      if area else '目前缺少有效支撐參考，暫無回檔型進場候選')
        else:
            state = p['interaction_state']
            if state == 'TESTING_RESISTANCE':
                detail = '目前正在測試 ' + area + ' 壓力區；尚未完成有效突破確認'
            elif state == 'RESISTANCE_BREAKOUT_PENDING':
                detail = area + ' 已突破，尚未完成有效突破確認'
            elif state == 'RESISTANCE_BREAKOUT_CONFIRMED':
                detail = area + ' 壓力區已完成突破確認'
            elif state == 'BELOW_RESISTANCE':
                detail = '目前上方 ' + area + ' 為最近有效壓力，尚未完成突破確認'
            elif key == 'pullback':
                detail = '觀察 ' + area + ' 支撐；' + '、'.join(p['missing'][:2])
            else:
                detail = '觀察 ' + area + ' 壓力；' + '、'.join(p['missing'][:2])
            if p['status'] == S.WATCHING and '趨勢尚未符合此路徑條件' in p['missing']:
                detail += '；趨勢尚未符合此路徑條件'
            detail += f"；進場確認須連續 {p['confirmation_required']} 根新收盤日線符合此路徑條件"
            if p['risk_blockers']:
                detail += '且風險限制解除'
        return prefix + detail
    primary = paths['primary_path']
    other = 'pullback' if primary == 'breakout' else 'breakout'
    return clause(primary, True) + '。\n' + clause(other, False) + '。'


def format_operation_reference(d, *, debug=False):
    from app.support_resistance_analysis.interaction import LevelInteractionState as I
    from app.support_resistance_analysis.interaction_formatting import interaction_guidance, interaction_text
    from app.decision_transitions import TriggerCode as T, zone_text
    from app.decision_evidence import RISK_STATES, evidence_text, valid
    presentation_warnings = list(d.consistency_warnings)
    unsupported = (d.holder_action in RISK_STATES and not (
        d.state_basis == 'DIRECT_RULE_MATCH' and valid(d.current_trigger_evidence, str(d.holder_action)) or
        d.state_basis == 'RETAINED_PENDING_CONFIRMATION' and valid(d.retained_state_evidence, str(d.holder_action))))
    if unsupported:
        presentation_warnings.append(str(d.holder_action) + '_WITHOUT_TRIGGER_EVIDENCE')
    def find(triggers, code):
        return next((t for t in triggers if t['code'] == code), None)
    def conditions(triggers):
        parts = []
        hold = find(triggers, T.SUPPORT_HOLD)
        reclaim = find(triggers, T.RECLAIM_KEY_LEVEL)
        if hold and hold.get('zone'):
            parts.append(zone_text(hold['zone']) + '確認守穩')
        if reclaim and reclaim.get('zone'):
            previous = d.price_context.get('previous_support_zone') or {}
            qualifier = '前支撐 ' if reclaim['zone'].get('zone_id') == previous.get('zone_id') else '壓力區 '
            interaction = reclaim['zone'].get('interaction') or {}
            state = interaction.get('state')
            if state == I.TESTING_RESISTANCE:
                parts.append('正在測試的壓力區 ' + zone_text(reclaim['zone']) + '上緣站穩並取得突破確認')
            elif state == I.RESISTANCE_BREAKOUT_PENDING:
                parts.append('已突破的壓力區 ' + zone_text(reclaim['zone']) + '取得有效突破確認')
            elif state == I.RESISTANCE_BREAKOUT_CONFIRMED:
                parts.append('已確認突破的原壓力區 ' + zone_text(reclaim['zone']) + '持續站穩')
            elif state == I.BELOW_RESISTANCE:
                parts.append('接近壓力區 ' + zone_text(reclaim['zone']) + '後取得突破確認')
            else:
                parts.append('重新站上' + qualifier + zone_text(reclaim['zone']))
        breakout = find(triggers, T.VOLUME_CONFIRMED_BREAKOUT)
        if breakout and breakout.get('zone'):
            # Reclaim and breakout describe the same reference; keep one condition.
            if reclaim and reclaim.get('zone') == breakout['zone']:
                # The state-aware reclaim clause already expresses confirmation.
                if not breakout['zone'].get('interaction'):
                    parts = [p for p in parts if not p.startswith('重新站上')]
                    parts.append(zone_text(breakout['zone']) + '放量突破獲得確認')
            else:
                parts.append(zone_text(breakout['zone']) + '放量突破獲得確認')
        if find(triggers, T.OVEREXTENSION_COOLING):
            parts.append('正乖離降回設定範圍')
        if not parts:
            parts.append('短期空方動能減弱且中期結構不再轉弱' if find(triggers, T.BEARISH_MOMENTUM_WEAKENING)
                         else '短中期方向維持偏多')
        return '，並'.join(parts[:2])
    entry_condition = conditions(d.entry_upgrade_triggers)
    support = d.price_context.get('active_support_zone')
    current_broken = d.price_context.get('current_active_support_status') in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
    if support:
        area = zone_text(support)
        defense = (f'{area} 已失守，先以站回該區為修復條件' if current_broken
                   else f'{area} 為目前防守區')
        worsen = (f'若無法站回 {area} 且中期結構續弱，風險進一步升高' if current_broken
                  else f'若 {area} 有效跌破且無法站回，風險進一步升高')
        interaction = support.get('interaction')
        if interaction:
            defense = (area + ' 為目前防守區（' + interaction_text(interaction) + '）'
                       if interaction['state'] in (I.ABOVE_SUPPORT, I.TESTING_SUPPORT)
                       else area + '：' + interaction_text(interaction))
            if interaction['state'] == I.SUPPORT_BREAKDOWN_PENDING:
                worsen = f'若 {area} 後續確認失守且無法站回，風險進一步升高'
    else:
        defense = '目前缺少可確認的支撐價位'
        worsen = '若中期轉空且短期續弱，風險進一步升高'
    pending = find(d.entry_upgrade_triggers, T.CONSECUTIVE_CONFIRMATION)
    count = (pending or {}).get('required_observations', 2)
    gated = bool(d.risk_gate)
    gate_clause = '且風險限制解除' if gated else ''
    if d.entry_action in (A.AVOID, A.WAIT, A.DO_NOT_CHASE):
        attitude = {A.AVOID: '目前不急於承接', A.WAIT: '目前維持觀望', A.DO_NOT_CHASE: '目前不宜追價'}[d.entry_action]
        entry = f'{attitude}。先以{entry_condition}{gate_clause}，作為轉入進場確認的必要條件。'
    elif d.entry_action == A.WATCH_FOR_CONFIRMATION:
        entry = f'目前等待進場確認。需{entry_condition}{gate_clause}，積極進場另須連續 {count} 根新收盤日線符合條件。'
    elif d.entry_action == A.ALLOW_PROBE_ENTRY:
        entry = f'目前僅適合小幅試單，升級進場須{entry_condition}。{worsen}，應停止提高進場積極度。'
    else:
        entry = f'目前進場條件成立，仍以{zone_text(support) + "守穩" if support else "短中期維持偏多"}為條件。{worsen}，需下調進場狀態。'
    if d.entry_paths:
        entry = format_entry_paths(d.entry_paths)
        previous = d.price_context.get('previous_support_zone')
        if previous and R.PREVIOUS_SUPPORT_BREAK in d.risk_gate:
            entry = '前支撐 ' + zone_text(previous) + ' 失守風險尚未解除；' + entry
    improve = conditions(d.holder_improve_triggers)
    if d.holder_action == H.HOLD:
        holder = f'{defense}，守穩時維持續抱狀態。{worsen}，需提高警戒。'
    elif d.holder_action == H.EXIT_CONDITION_APPROACHING:
        holder = f'目前已接近退出風險條件；{defense}。只有{improve}{gate_clause}並獲新日線連續確認，風險才具備下調條件。'
    else:
        holder = f'{defense}；{worsen}。若{improve}{gate_clause}並獲新日線連續確認，則具備降低警戒的條件。'
    if d.holder_action in RISK_STATES:
        evidence = d.current_trigger_evidence
        if d.state_basis == 'RETAINED_PENDING_CONFIRMATION' and not unsupported:
            explanation = f'原風險條件已部分緩解，但改善確認尚未完成，因此暫時維持{HOLDER_LABELS[d.holder_action]}狀態。'
        elif d.state_basis == 'DIRECT_RULE_MATCH' and valid(evidence, str(d.holder_action)):
            primary = evidence_text(evidence.get('primary_trigger'))
            supporting = [evidence_text(e) for e in evidence.get('supporting_evidence', [])[:2]]
            explanation = '目前已成立：' + primary + ('；輔助依據：' + '、'.join(supporting) if supporting else '') + '。'
            explanation += '目前進入' + HOLDER_LABELS[d.holder_action] + ('評估。' if d.holder_action == H.REDUCE_EXPOSURE else '狀態。')
            if d.holder_action == H.EXIT_CONDITION_APPROACHING:
                explanation += '尚未確認反彈失敗，不代表出清條件已成立。'
        else:
            explanation = '目前缺少可驗證的觸發證據，無法支持此風險狀態。'
        holder = explanation + '\n' + defense + '；' + worsen + '。若' + improve + gate_clause + '並獲新日線連續確認，則具備降低警戒的條件。'
    elif d.state_basis == 'INSUFFICIENT_EVIDENCE' and d.consistency_warnings:
        holder = '缺少有效風險觸發證據，已回退為謹慎續抱；' + holder
    output = dict(for_non_holder=entry, for_holder=holder, observation_conditions=[],
                  entry_label=ENTRY_LABELS[d.entry_action], holder_label=HOLDER_LABELS[d.holder_action])
    interactions = d.price_context.get('level_interactions', [])
    # Include crossed zones even though lifecycle correctly excludes them from
    # active defense/resistance roles. Select at most one nearest zone per role.
    for role, target in (('resistance', 'for_non_holder'), ('support', 'for_holder')):
        if role == 'resistance' and d.entry_paths:
            continue
        relevant = [i for i in interactions if i['role'] == role]
        if role == 'support' and support:
            relevant = [i for i in relevant if i.get('zone_low') == support['low']
                        and i.get('zone_high') == support['high']]
        nearest = min(relevant, key=lambda i: abs(i['distance_pct'] or 0), default=None)
        if nearest and nearest['state'] not in (I.ABOVE_SUPPORT, I.BELOW_RESISTANCE):
            output[target] = interaction_guidance({'interaction': nearest}) + '\n' + output[target]
    if unsupported:
        output['holder_label'] = '證據不足'  # Rendering cannot repair an old, unvalidated decision payload.
    if R.DATA_INSUFFICIENT in d.entry_reasons:
        output['for_non_holder'] = '分析資料不足，維持觀望；待價格與必要風險資料補齊後，重新檢查進場確認條件。'
    if R.DATA_INSUFFICIENT in d.holder_reasons:
        output['for_holder'] = '分析資料不足，目前無法可靠判斷市場風險；待支撐價位與有效收盤資料補齊後，再確認防守區是否守穩。'
    changes = []
    for key, current, labels, role in (
        ('entry_state', d.entry_action, ENTRY_LABELS, '空手'),
        ('holder_state', d.holder_action, HOLDER_LABELS, '持有')):
        previous = d.previous_action_state.get(key)
        if previous in labels and previous != current:
            changes.append(f'{role}：{labels[previous]} → {labels[current]}')
    if d.transition_events:
        blocks = ['操作狀態變化：']
        for event in d.transition_events:
            labels = ENTRY_LABELS if event['role'] == 'ENTRY' else HOLDER_LABELS
            role = '空手' if event['role'] == 'ENTRY' else '持有'
            blocks.append(f"{role}：{labels[event['previous_state']]} → {labels[event['current_state']]}")
            blocks.append('主要狀態變化依據：' + event['primary_transition_reason']['text'])
            for reason in event['supporting_transition_reasons'][:2]:
                blocks.append('・輔助依據：' + reason['text'])
        output['state_change'] = '\n'.join(blocks)
    elif changes and (not d.transition_debug or d.transition_debug.get('status') == 'BASELINE_UNAVAILABLE'):
        output['state_change'] = '操作狀態變化：' + '；'.join(changes) + '\n缺少可比較的前次快照，無法可靠歸因。'
    if debug:
        import json
        output['debug'] = '\n'.join(('[DEBUG]', f'Entry State: {d.entry_action}',
            f'Holder State: {d.holder_action}', f'Entry Score: {d.entry_score}',
            f'Hold Risk Score: {d.risk_score}', 'Risk Gate: ' + ', '.join(d.risk_gate),
            'Entry Reasons: ' + ', '.join(d.entry_reasons), 'Holder Reasons: ' + ', '.join(d.holder_reasons),
            'Transition Triggers: ' + json.dumps({key: getattr(d, key) for key in (
                'entry_upgrade_triggers', 'entry_downgrade_triggers', 'holder_improve_triggers', 'holder_worsen_triggers')}, ensure_ascii=False),
            'Price Context: ' + json.dumps(d.price_context, ensure_ascii=False)))
        output['debug'] += '\n[TRANSITION DEBUG]\n' + json.dumps(d.transition_debug, ensure_ascii=False, indent=2)
        output['debug'] += '\n[ENTRY PATHS]\n' + json.dumps(d.entry_paths, ensure_ascii=False, indent=2)
        output['debug'] += '\n[CURRENT ACTION EVIDENCE]\n' + json.dumps(dict(
            holder_state=d.holder_action, state_basis=d.state_basis,
            current_trigger_evidence=d.current_trigger_evidence, retained_state_evidence=d.retained_state_evidence,
            current_defense_zone=d.price_context.get('active_support_zone'),
            worsen_conditions=d.holder_worsen_triggers, improve_conditions=d.holder_improve_triggers,
            consistency_warnings=presentation_warnings), ensure_ascii=False, indent=2)
    return output
