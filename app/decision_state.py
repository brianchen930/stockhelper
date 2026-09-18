"""Small per-symbol state; read-only analysis never advances monitoring counts."""
from dataclasses import replace, asdict
from app.decision_engine import ActionState as A, HolderActionState as H, ReasonCode as R, DecisionConfig


def stabilize(decision, context, previous, config=None):
    config = config or DecisionConfig()
    from app.decision_transitions import attach_triggers
    from app.decision_evidence import reconcile
    d = replace(decision, entry_reasons=list(decision.entry_reasons), holder_reasons=list(decision.holder_reasons))
    old = previous or {}
    if (old.get('last_observation_time') and context.observation_time
            and context.observation_time < old['last_observation_time']):
        d.entry_action = A(old['entry_state'])
        d.holder_action = H(old['holder_state'])
        d.entry_reasons = d.holder_reasons = [R.DATA_INSUFFICIENT]
        d.confirmation_count = old.get('confirmation_count', 0)
        d.current_trigger_evidence = {}
        d.state_basis = 'STALE_DATA'
        if d.entry_paths:
            from app.entry_paths import suspend_paths
            d.entry_paths = suspend_paths(d.entry_paths)
        return attach_triggers(d, context, config), dict(old)
    d.previous_action_state = {k: old[k] for k in ('entry_state', 'holder_state') if old.get(k)}
    candidate = str(d.entry_action)
    if d.entry_paths:
        from app.entry_paths import entry_action
        eligible = {k: {'ready': d.entry_paths[k]['eligible']} for k in ('breakout', 'pullback')}
        candidate = str(entry_action(eligible, d.entry_action, context.volatility_level))
    count = old.get('confirmation_count', 0)
    new = (context.data_valid and context.observation_complete and context.observation_time
           and (not old.get('last_observation_time') or context.observation_time > old['last_observation_time']))
    aggressive = candidate in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)
    if new:
        count = min(count + 1, config.confirmation_required) if candidate == old.get('candidate_entry_state') else 1
    elif candidate != old.get('candidate_entry_state'):
        count = 0
    if aggressive and count < config.confirmation_required:
        d.entry_action = A.WATCH_FOR_CONFIRMATION
        d.entry_reasons.append(R.CONFIRMATION_PENDING)
    path_memory = None
    if d.entry_paths:
        from app.entry_paths import stabilize_paths, entry_action
        d.entry_paths, path_memory = stabilize_paths(d.entry_paths, context, old, config)
        d.entry_action = entry_action(d.entry_paths, d.entry_action, context.volatility_level)
        count = max(p['confirmation_count'] for k, p in d.entry_paths.items()
                    if k in ('breakout', 'pullback'))
        if d.entry_paths['entry_ready']:
            d.entry_reasons = [r for r in d.entry_reasons if r != R.CONFIRMATION_PENDING]
    # Improving holder risk requires two new observations; deterioration is immediate.
    holder_order = list(H)
    old_holder = old.get('holder_state')
    candidate_holder = str(d.holder_action)
    holder_count = old.get('holder_confirmation_count', 0)
    if new:
        holder_count = min(holder_count + 1, config.confirmation_required) if candidate_holder == old.get('candidate_holder_state') else 1
    elif candidate_holder != old.get('candidate_holder_state'):
        holder_count = 0
    if old_holder in holder_order and holder_order.index(d.holder_action) < holder_order.index(old_holder) and holder_count < config.confirmation_required:
        d.holder_action = H(old_holder)
        d.holder_reasons.append(R.CONFIRMATION_PENDING)
    d.confirmation_count = count
    state = dict(symbol=context.symbol, entry_state=str(d.entry_action), holder_state=str(d.holder_action),
                 candidate_entry_state=candidate, candidate_holder_state=candidate_holder,
                 confirmation_count=count, holder_confirmation_count=holder_count,
                 last_observation_time=context.observation_time if new else old.get('last_observation_time'))
    if path_memory is not None:
        state['entry_path_memory'] = path_memory
    d = reconcile(d, decision, context, old, state, config)
    return attach_triggers(d, context, config), state


def create_table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS decision_state (
        symbol TEXT PRIMARY KEY, entry_state TEXT, holder_state TEXT,
        candidate_entry_state TEXT, candidate_holder_state TEXT,
        confirmation_count INTEGER NOT NULL DEFAULT 0,
        holder_confirmation_count INTEGER NOT NULL DEFAULT 0,
        last_observation_time TEXT)''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(decision_state)')}
    for name in ('previous_context_summary', 'context_fingerprint', 'holder_evidence_summary', 'entry_path_memory'):
        if name not in columns:
            connection.execute(f'ALTER TABLE decision_state ADD COLUMN {name} TEXT')
    connection.execute('''CREATE TABLE IF NOT EXISTS decision_transition_events (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL, role TEXT NOT NULL,
        previous_state TEXT NOT NULL, current_state TEXT NOT NULL,
        data_timestamp TEXT NOT NULL, observation_fingerprint TEXT NOT NULL,
        timestamp TEXT NOT NULL, event_json TEXT NOT NULL,
        UNIQUE(symbol, role, previous_state, current_state, data_timestamp, observation_fingerprint)
    )''')


def update_monitor_decision(data, connection=None):
    import json
    from app.decision_transition_analyzer import (context_summary, context_fingerprint,
                                                 analyze_transition, canonical)
    from app.decision_engine import DecisionContext, DecisionEngine
    from app.decision_formatter import format_operation_reference
    from app.database import get_connection
    if not data.get('decision_context'):
        return
    context = DecisionContext(**data['decision_context'])
    if not context.data_valid:
        return
    owned = connection is None
    connection = connection or get_connection()
    try:
        create_table(connection)
        connection.execute('BEGIN IMMEDIATE')
        cursor = connection.execute('SELECT * FROM decision_state WHERE symbol = ?', (context.symbol,))
        row = cursor.fetchone()
        previous = dict(zip([col[0] for col in cursor.description], row)) if row else {}
        decision, state = stabilize(DecisionEngine().evaluate(context), context, previous)
        try:
            old_summary = json.loads(previous.get('previous_context_summary') or 'null')
        except (ValueError, TypeError):
            old_summary = None
        old_time = (old_summary or {}).get('context', {}).get('observation_time') or previous.get('last_observation_time')
        stale = bool(old_time and context.observation_time and context.observation_time < old_time)
        if stale:
            # An old daily bar must not replace a newer incomplete-bar snapshot either.
            decision.entry_action = A(previous['entry_state'])
            decision.holder_action = H(previous['holder_state'])
            decision.previous_action_state = {k: previous[k] for k in ('entry_state', 'holder_state')}
            state = dict(previous)
            decision.current_trigger_evidence = {}
            decision.retained_state_evidence = {}
            decision.state_basis = 'STALE_DATA'
            decision.transition_debug = {'status': 'STALE_OBSERVATION_IGNORED'}
            if decision.entry_paths:
                from app.entry_paths import suspend_paths
                decision.entry_paths = suspend_paths(decision.entry_paths)
        else:
            fingerprint = context_fingerprint(context)
            if (fingerprint == previous.get('context_fingerprint') and previous.get('holder_evidence_summary')
                    and previous.get('entry_path_memory')):
                # Pure replay: preserve stabilization memory as well as final actions.
                decision.entry_action = A(previous['entry_state'])
                decision.holder_action = H(previous['holder_state'])
                decision.confirmation_count = previous['confirmation_count']
                state = dict(previous)
                from app.decision_evidence import load_memory
                memory = load_memory(previous)
                if memory:
                    decision.current_trigger_evidence = memory.get('current_trigger_evidence', {})
                    decision.retained_state_evidence = memory.get('retained_state_evidence', {})
                    decision.state_basis = memory.get('state_basis', 'INSUFFICIENT_EVIDENCE')
            summary = context_summary(context, decision, state)
            events, decision.transition_debug = analyze_transition(old_summary, summary, context.symbol)
            if fingerprint == previous.get('context_fingerprint'):
                decision.transition_debug['status'] = 'PURE_REPLAY'
            for event in events:
                payload = asdict(event)
                inserted = connection.execute('''INSERT INTO decision_transition_events
                    (symbol, role, previous_state, current_state, data_timestamp,
                     observation_fingerprint, timestamp, event_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING''',
                    (event.symbol, event.role, event.previous_state, event.current_state,
                     event.data_timestamp or '', event.observation_fingerprint, event.timestamp, canonical(payload)))
                if inserted.rowcount:
                    decision.transition_events.append(payload)
            decision.transition_debug['deduplicated_events'] = len(events) - len(decision.transition_events)
            state['previous_context_summary'] = canonical(summary)
            state['context_fingerprint'] = fingerprint
        # Reattach future conditions if replay/stale handling restored final actions.
        from app.decision_transitions import attach_triggers
        decision = attach_triggers(decision, context)
        columns = list(state)
        updates = ','.join(f'{key}=excluded.{key}' for key in columns if key != 'symbol')
        connection.execute('INSERT INTO decision_state (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ') ON CONFLICT(symbol) DO UPDATE SET ' + updates, tuple(state.values()))
        connection.commit()
        data['trading_decision'] = asdict(decision)
        data['timeframe_analysis']['trading_decision'] = asdict(decision)
        data['timeframe_analysis']['operation_reference'] = format_operation_reference(decision)
    except Exception:
        connection.rollback()
        raise
    finally:
        if owned:
            connection.close()
