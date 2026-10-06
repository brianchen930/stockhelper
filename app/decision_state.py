"""Small per-symbol state; read-only analysis never advances monitoring counts."""
from dataclasses import replace, asdict
from app.decision_engine import ActionState as A, HolderActionState as H, ReasonCode as R, DecisionConfig


def stabilize(decision, context, previous, config=None, *, signal_observation_time=None, signal_read_only=False):
    config = config or DecisionConfig()
    from app.decision_transitions import attach_triggers
    from app.decision_evidence import reconcile
    from app.holder_structure import resolve_structure, structure_memory
    resolved = resolve_structure(context, previous, config)
    if (decision.price_context.get('support_policy') == 'STRUCTURAL_V1' and any(
            getattr(resolved, key) != decision.price_context.get(key) for key in
            ('breakout_event', 'structural_support_zone', 'structural_support_status'))):
        from app.decision_engine import DecisionEngine
        decision = DecisionEngine(config).evaluate(resolved, previous)
    context = resolved
    d = replace(decision, entry_reasons=list(decision.entry_reasons), holder_reasons=list(decision.holder_reasons))
    old = previous or {}
    d.previous_action_state = {k: old[k] for k in ('entry_state', 'holder_state', 'signal_state') if old.get(k)}
    if (old.get('last_observation_time') and context.observation_time
            and context.observation_time < old['last_observation_time']):
        d.entry_action = (A.WATCH_FOR_CONFIRMATION if old['entry_state'] in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)
                          else A(old['entry_state']))
        d.holder_action = H(old['holder_state'])
        d.entry_reasons = d.holder_reasons = [R.DATA_INSUFFICIENT]
        d.confirmation_count = old.get('confirmation_count', 0)
        d.current_trigger_evidence = {}
        d.state_basis = 'STALE_DATA'
        if d.entry_paths:
            from app.entry_paths import suspend_paths
            d.entry_paths = suspend_paths(d.entry_paths)
        from app.decision_engine import DecisionEngine
        DecisionEngine(config).apply_signal_hysteresis(d, context, d.previous_action_state)
        DecisionEngine(config).apply_signal_persistence(d, context, old, observation_time=signal_observation_time, read_only=signal_read_only)
        return DecisionEngine(config).evaluate_trade(attach_triggers(d, context, config), context), dict(old)
    candidate = str(d.entry_action)
    if d.entry_paths:
        from app.entry_paths import entry_action
        eligible = {k: dict(d.entry_paths[k], ready=d.entry_paths[k]['eligible']) for k in ('breakout', 'pullback')}
        candidate = str(entry_action(eligible, d.entry_action, context.volatility_level))
    count = old.get('confirmation_count', 0)
    new = (context.data_valid and context.observation_complete and context.observation_time
           and (not old.get('last_observation_time') or context.observation_time > old['last_observation_time']))
    aggressive = candidate in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)
    if new:
        count = min(count + 1, config.confirmation_required) if candidate == old.get('candidate_entry_state') else 1
    elif candidate != old.get('candidate_entry_state'):
        count = 0
    if not d.entry_paths and aggressive and count < config.confirmation_required:
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
        elif any(d.entry_paths[k]['eligible'] for k in ('breakout', 'pullback')):
            d.entry_reasons.append(R.CONFIRMATION_PENDING)
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
    state['holder_structure_memory'] = structure_memory(context, old)
    d = reconcile(d, decision, context, old, state, config)
    d = stabilize_trade(attach_triggers(d, context, config), context, old, state, config)
    from app.decision_engine import DecisionEngine
    DecisionEngine(config).apply_signal_hysteresis(d, context, d.previous_action_state)
    state.update(DecisionEngine(config).apply_signal_persistence(
        d, context, old, observation_time=signal_observation_time, read_only=signal_read_only))
    return d, state


def load_decision_state(symbol):
    """Read existing monitoring memory without creating or migrating a database."""
    import sqlite3
    from app.database import DATABASE_PATH
    if not symbol or not DATABASE_PATH.exists():
        return {}
    connection = sqlite3.connect(DATABASE_PATH.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_state'").fetchone():
            return {}
        cursor = connection.execute('SELECT * FROM decision_state WHERE symbol = ?', (symbol,))
        row = cursor.fetchone()
        return dict(zip([col[0] for col in cursor.description], row)) if row else {}
    finally:
        connection.close()


def publish_decision(data, decision):
    """Publish one finalized decision to every output consumer."""
    from app.decision_formatter import format_operation_reference
    snapshot = asdict(decision)
    data['trading_decision'] = snapshot
    if data.get('analysis') and decision.final_action_state in ('偏多', '觀望', '偏空'):
        data['analysis']['raw_signal'] = decision.raw_action_state
        data['analysis']['signal'] = decision.final_action_state
    timeframe = data.setdefault('timeframe_analysis', {})
    timeframe['trading_decision'] = snapshot
    timeframe['operation_reference'] = format_operation_reference(decision)


def stabilize_trade(d, context, previous, state, config=None):
    import json
    from app.decision_engine import DecisionEngine
    from app.entry_paths import confirmation_count
    config = config or DecisionConfig()
    d = DecisionEngine(config).evaluate_trade(d, context)
    try:
        old = json.loads(previous.get('trade_memory') or '{}')
    except (ValueError, TypeError):
        old = {}
    zone = context.structural_support_zone or {}
    identity = [d.position_status, zone.get('stable_zone_id') or zone.get('zone_id') or [zone.get('low'), zone.get('high')]]
    order = {'HOLD': 0, 'OBSERVE': 1, 'CONSIDER_REDUCE': 2, 'REDUCE': 3, 'EXIT': 4}
    # Legacy positive actions have no selling risk to retain. Old risk states
    # remain readable; only new weighted observations advance their recovery.
    old_action = {'ADD': 'HOLD', 'ENTER': 'OBSERVE', 'WAIT': 'OBSERVE'}.get(old.get('decision'), old.get('decision'))
    if old.get('support_policy') != 'STRUCTURAL_V1':
        old_action = None  # Old nearest-support reductions cannot survive the policy migration.
    candidate = d.decision
    recovering = (d.position_status == 'HOLDING' and old_action in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT')
                  and order[candidate] < order[old_action]
                  and d.trade_evidence['checks']['valid'])
    recovery_old = old.get('recovery', {})
    recovery_count, recovery_stamp = confirmation_count(recovering, identity + [candidate, 'weighted_v4'], context,
        recovery_old, recovery_old.get('last_observation_time') or old.get('last_observation_time'), config)
    if recovering and recovery_count < config.confirmation_required:
        # An old EXIT can ease to REDUCE immediately; full recovery still
        # requires consecutive new closes. Never retain EXIT without evidence.
        d.decision = 'REDUCE' if old_action == 'EXIT' else old_action
        # Confirmation metadata is rendered under follow-up, never as a market
        # reason. Preserve the current support/trend observation conditions.
    pending = recovering and recovery_count < config.confirmation_required
    # Completed confirmation is stored in memory; the public pending counter
    # resets immediately so replaying that close cannot create a new history row.
    d.trade_confirmation_count = recovery_count if pending else 0
    d.trade_evidence['recovery'] = dict(pending=pending,
        previous_action=old_action if pending else None, candidate=candidate,
        count=d.trade_confirmation_count, required=config.confirmation_required)
    state['trade_memory'] = json.dumps(dict(version=4, support_policy='STRUCTURAL_V1', identity=identity, last_observation_time=recovery_stamp,
        decision=d.decision, recovery=dict(identity=identity + [candidate, 'weighted_v4'], count=recovery_count,
                                           last_observation_time=recovery_stamp)), ensure_ascii=False)
    return d


def create_table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS decision_state (
        symbol TEXT PRIMARY KEY, entry_state TEXT, holder_state TEXT,
        candidate_entry_state TEXT, candidate_holder_state TEXT,
        confirmation_count INTEGER NOT NULL DEFAULT 0,
        holder_confirmation_count INTEGER NOT NULL DEFAULT 0,
        last_observation_time TEXT)''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(decision_state)')}
    for name in ('previous_context_summary', 'context_fingerprint', 'holder_evidence_summary', 'holder_structure_memory', 'entry_path_memory', 'trade_memory', 'signal_state',
                 'signal_previous_action_state', 'signal_pending_candidate_state', 'signal_last_observation_time'):
        if name not in columns:
            connection.execute(f'ALTER TABLE decision_state ADD COLUMN {name} TEXT')
    if 'signal_confirmation_count' not in columns:
        connection.execute('ALTER TABLE decision_state ADD COLUMN signal_confirmation_count INTEGER NOT NULL DEFAULT 0')
    connection.execute('''CREATE TABLE IF NOT EXISTS decision_transition_events (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL, role TEXT NOT NULL,
        previous_state TEXT NOT NULL, current_state TEXT NOT NULL,
        data_timestamp TEXT NOT NULL, observation_fingerprint TEXT NOT NULL,
        timestamp TEXT NOT NULL, event_json TEXT NOT NULL,
        UNIQUE(symbol, role, previous_state, current_state, data_timestamp, observation_fingerprint)
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS trading_decision_history (
        id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
        position_status TEXT NOT NULL, decision TEXT NOT NULL,
        decision_reasons TEXT NOT NULL, warnings TEXT NOT NULL,
        context_json TEXT NOT NULL, decision_json TEXT NOT NULL,
        fingerprint TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(symbol, date, position_status, fingerprint))''')


def update_monitor_decision(data, connection=None, *, previous_action_state=None, config=None, signal_observation_time=None):
    import json
    from app.decision_transition_analyzer import (context_summary, context_fingerprint,
                                                 analyze_transition, canonical)
    from app.decision_engine import DecisionContext, DecisionEngine
    from app.database import get_connection
    if not data.get('decision_context'):
        return
    context = DecisionContext(**data['decision_context'])
    if not context.data_valid:
        return
    config = config or DecisionConfig()
    engine = DecisionEngine(config)
    from datetime import datetime, timezone
    signal_observation_time = signal_observation_time or datetime.now(timezone.utc).isoformat()
    owned = connection is None
    connection = connection or get_connection()
    try:
        create_table(connection)
        connection.execute('BEGIN IMMEDIATE')
        cursor = connection.execute('SELECT * FROM decision_state WHERE symbol = ?', (context.symbol,))
        row = cursor.fetchone()
        previous = dict(zip([col[0] for col in cursor.description], row)) if row else {}
        from app.holder_structure import resolve_structure
        context = resolve_structure(context, previous, config)
        data['decision_context'] = asdict(context)
        # Seed existing installations from watchlist.last_signal only once.
        # Thereafter the per-symbol DecisionEngine state is authoritative.
        if not previous.get('signal_state') and previous_action_state:
            previous['signal_state'] = previous_action_state.get('signal_state')
        decision, state = stabilize(engine.evaluate(context), context, previous, config,
                                    signal_observation_time=signal_observation_time)
        try:
            old_summary = json.loads(previous.get('previous_context_summary') or 'null')
        except (ValueError, TypeError):
            old_summary = None
        old_time = (old_summary or {}).get('context', {}).get('observation_time') or previous.get('last_observation_time')
        stale = bool(old_time and context.observation_time and context.observation_time < old_time)
        if stale:
            # An old daily bar must not replace a newer incomplete-bar snapshot either.
            decision.entry_action = (A.WATCH_FOR_CONFIRMATION if previous['entry_state'] in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)
                                     else A(previous['entry_state']))
            decision.holder_action = H(previous['holder_state'])
            decision.previous_action_state = {k: previous[k] for k in ('entry_state', 'holder_state', 'signal_state') if previous.get(k)}
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
                    and previous.get('entry_path_memory') == state.get('entry_path_memory')):
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
            summary = context_summary(context, decision, state, config)
            events, decision.transition_debug = analyze_transition(
                old_summary, summary, context.symbol, timestamp=signal_observation_time)
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
        decision = attach_triggers(decision, context, config)
        engine.apply_signal_hysteresis(decision, context, decision.previous_action_state)
        signal_memory = engine.apply_signal_persistence(
            decision, context, previous, observation_time=signal_observation_time)
        if stale:
            decision = engine.evaluate_trade(decision, context)
        else:
            # Always use the pre-round snapshot: stabilize and this rendering
            # pass must not count as two independent monitoring observations.
            state.update(signal_memory)
            decision = stabilize_trade(decision, context, previous, state, config)
            if context.observation_complete and context.observation_time:
                import hashlib
                payload = canonical(dict(context=asdict(context), decision=asdict(decision), config=asdict(config)))
                # Stable snapshot identity excludes transition diagnostics and replay labels.
                identity = canonical(dict(context=asdict(context), decision=decision.decision,
                    reasons=decision.reasons, warnings=decision.warnings, evidence=decision.trade_evidence,
                    confirmation_count=decision.trade_confirmation_count, signal_state=decision.final_action_state,
                    signal_pending_candidate_state=decision.pending_candidate_state,
                    signal_confirmation_count=decision.signal_confirmation_count,
                    config=asdict(config), version=1))
                digest = hashlib.sha256(identity.encode()).hexdigest()
                connection.execute('''INSERT INTO trading_decision_history
                    (date,symbol,position_status,decision,decision_reasons,warnings,context_json,decision_json,fingerprint)
                    VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING''',
                    (context.observation_time, context.symbol, decision.position_status, decision.decision,
                     canonical(decision.reasons), canonical(decision.warnings), canonical(asdict(context)), payload, digest))
        columns = list(state)
        updates = ','.join(f'{key}=excluded.{key}' for key in columns if key != 'symbol')
        connection.execute('INSERT INTO decision_state (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ') ON CONFLICT(symbol) DO UPDATE SET ' + updates, tuple(state.values()))
        connection.commit()
        publish_decision(data, decision)
    except Exception:
        connection.rollback()
        raise
    finally:
        if owned:
            connection.close()
