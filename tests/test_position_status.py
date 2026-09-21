import importlib
import sqlite3
from dataclasses import asdict, replace

import pandas as pd
import pytest
from app.position_status import POSITION_FIELDS, calculate_unrealized_pnl, format_position
from fastapi.testclient import TestClient

from app import database
from app.position_status import PositionStatus, format_position_status


DETAILS = dict(average_cost=480.5, shares=100, entry_date='2026-09-10')


def test_position_details_create_partial_update_and_clear(client, db):
    watching = client.post('/watchlist', json={'stock_code': '2454'}).json()
    assert all(watching[key] is None for key in POSITION_FIELDS)
    holding = client.post('/watchlist', json=dict(stock_code='2408', position_status='HOLDING', **DETAILS))
    assert holding.status_code == 200
    assert all(holding.json()[key] == value for key, value in DETAILS.items())
    assert all(db.get_stock('2408')[key] == value for key, value in DETAILS.items())
    changed = client.patch('/watchlist/2408', json={'average_cost': 492, 'shares': 150}).json()
    assert changed['average_cost'] == 492 and changed['shares'] == 150
    assert changed['entry_date'] == DETAILS['entry_date'] and changed['position_status'] == 'HOLDING'
    odd = client.patch('/watchlist/2408', json={'shares': 17}).json()
    assert odd['shares'] == 17 and odd['average_cost'] == 492
    assert client.patch('/watchlist/2408', json={'entry_date': None}).json()['entry_date'] is None
    promoted = client.patch('/watchlist/2454', json=dict(position_status='HOLDING', average_cost=1300, shares=20, entry_date='2026-09-18')).json()
    assert promoted['average_cost'] == 1300 and promoted['shares'] == 20
    cleared = client.patch('/watchlist/2454', json={'position_status': 'WATCHING'}).json()
    assert all(cleared[key] is None for key in POSITION_FIELDS)
    assert all(db.get_stock('2454')[key] is None for key in POSITION_FIELDS)
    # Compatibility wrapper must also clear position fields.
    db.update_position_status('2408', 'WATCHING')
    assert all(db.get_stock('2408')[key] is None for key in POSITION_FIELDS)


@pytest.mark.parametrize('field,value', [
    ('average_cost', -100), ('average_cost', 0), ('average_cost', '480.5'),
    ('average_cost', True), ('shares', 0), ('shares', -1), ('shares', 1.5),
    ('shares', 17.0), ('shares', '17'), ('shares', True), ('shares', 2**63),
    ('entry_date', '2026-99-99'), ('entry_date', '09/18/2026'), ('entry_date', 'abc'),
    ('entry_date', '2026-02-29'), ('entry_date', '2026-9-18'),
    ('entry_date', '2026-09-18T00:00:00'), ('entry_date', 20260918),
])
def test_invalid_details_rejected_at_api_and_database(client, db, field, value):
    db.add_stock('2408', position_status='HOLDING', **DETAILS)
    before = db.get_stock('2408')
    assert client.patch('/watchlist/2408', json={field: value}).status_code == 422
    assert client.post('/watchlist', json=dict(stock_code='2454', position_status='HOLDING', **{field: value})).status_code == 422
    with pytest.raises(ValueError):
        db.update_position('2408', **{field: value})
    with pytest.raises(ValueError):
        db.add_stock('2454', position_status='HOLDING', **{field: value})
    assert db.get_stock('2408') == before and db.get_stock('2454') is None


@pytest.mark.parametrize('cost', [float('nan'), float('inf'), float('-inf'), 10**400])
def test_nonfinite_cost_rejected(db, cost):
    with pytest.raises(ValueError):
        db.add_stock('2408', position_status='HOLDING', average_cost=cost)


@pytest.mark.parametrize('payload', [[], ['shares'], 123, 'abc'])
def test_non_object_request_rejected(client, payload):
    assert client.post('/watchlist', json=payload).status_code == 422
    assert client.patch('/watchlist/2408', json=payload).status_code == 422


def test_watching_clears_supplied_details_and_holding_can_be_incomplete(client, db):
    row = client.post('/watchlist', json=dict(stock_code='2454', **DETAILS)).json()
    assert all(row[key] is None for key in POSITION_FIELDS)
    row = client.patch('/watchlist/2454', json=DETAILS).json()
    assert all(row[key] is None for key in POSITION_FIELDS)
    assert format_position(row, 525) == ['持倉狀態：觀察中']
    row = client.patch('/watchlist/2454', json={'position_status': 'HOLDING'}).json()
    assert format_position(row, 525) == ['持倉狀態：已持有', '持倉資料：尚未完整設定']
    assert client.patch('/watchlist/2454', json={'entry_date': '2024-02-29'}).status_code == 200


@pytest.mark.parametrize('price,rate,amount,amount_text', [
    (110, 10, 500, '+500'), (90, -10, -500, '-500'),
    (100, 0, 0, '+0'), (100.25, .25, 12.5, '+12.5'),
])
def test_pnl_calculation_and_format(price, rate, amount, amount_text):
    row = dict(position_status='HOLDING', average_cost=100, shares=50, entry_date='2026-09-18')
    assert calculate_unrealized_pnl(row, price) == dict(unrealized_return_percent=rate, unrealized_pnl=amount)
    lines = format_position(row, price)
    assert f'未實現報酬：{rate:+.2f}%' in lines
    assert f'未實現損益：{amount_text} 元' in lines


def test_pnl_example_and_missing_price():
    row = dict(position_status='HOLDING', **DETAILS)
    lines = format_position(row, 525)
    assert lines == ['持倉狀態：已持有', '平均成本：480.5', '持有股數：100 股',
                     '買進日期：2026-09-10', '未實現報酬：+9.26%', '未實現損益：+4,450 元']
    for price in (None, 0, -1, float('nan'), float('inf')):
        assert all(value is None for value in calculate_unrealized_pnl(row, price).values())
        assert not any('未實現' in line for line in format_position(row, price))


def test_migrate_status_only_database(tmp_path, monkeypatch):
    path = tmp_path / 'status_only.db'
    monkeypatch.setattr(database, 'DATABASE_PATH', path)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE watchlist (id INTEGER PRIMARY KEY, stock_code TEXT UNIQUE, stock_name TEXT, created_at TEXT, position_status TEXT)')
        connection.execute("INSERT INTO watchlist VALUES (1, '2408', '南亞科', '2026-09-18', 'HOLDING')")
    connection.close()
    database.create_tables()
    database.create_tables()
    row = database.get_stock('2408')
    assert row['position_status'] == 'HOLDING' and row['stock_name'] == '南亞科'
    assert all(row[key] is None for key in POSITION_FIELDS)
    assert '持倉資料：尚未完整設定' in format_position(row)
    database.update_position('2408', **DETAILS)
    database.create_tables()
    assert all(database.get_stock('2408')[key] == value for key, value in DETAILS.items())


@pytest.mark.parametrize('empty', [False, True])
def test_details_reach_context_without_changing_market_analysis(client, db, market, empty):
    stock, frame = market
    if empty:
        frame.drop(frame.index, inplace=True)
    db.add_stock('2408', position_status='HOLDING', **DETAILS)
    first = client.get('/stock/2408/analysis').json()
    for container in (first, first['decision_context'], first['timeframe_analysis']['decision_context']):
        assert all(container[key] == value for key, value in DETAILS.items())
    monitor = client.get('/monitor').json()['results'][0]
    assert all(monitor[key] == value for key, value in DETAILS.items())
    assert all(monitor['data'][key] == value for key, value in DETAILS.items())
    db.update_position('2408', average_cost=10, shares=17, entry_date='2026-09-18')
    second = client.get('/stock/2408/analysis').json()
    for key in ('analysis', 'support_resistance', 'rsi', 'macd', 'kd_k', 'atr'):
        assert first.get(key) == second.get(key)
    for key in ('entry_action', 'holder_action', 'decision', 'risk_gate', 'entry_score', 'risk_score'):
        assert first['trading_decision'][key] == second['trading_decision'][key]
    if not empty:
        assert first['unrealized_pnl'] != second['unrealized_pnl']
    from app.decision_engine import DecisionContext
    from app.decision_transition_analyzer import context_fingerprint
    assert context_fingerprint(DecisionContext(**first['decision_context'])) == context_fingerprint(DecisionContext(**second['decision_context']))


def test_details_scheduler_output_and_live_price(db, market, monkeypatch, capsys):
    from app import scheduler
    stock, _ = market
    monkeypatch.setattr(stock, 'get_realtime_price', lambda code: dict(realtime_price=525, price_source='realtime'))
    db.add_stock('2408', '南亞科', position_status='HOLDING', **DETAILS)
    data = stock.analyze_watchlist(db.get_all_stocks())[0]['data']
    assert data['unrealized_pnl'] == 4450
    messages = []
    def capture(items):
        messages.extend(item['message'] for item in items)
        return dict(matched_count=len(items), success_count=len(items), failed_count=0,
                    failed_items=[], item_results=[dict(stock_code=item['stock_code'], success=True) for item in items])
    monkeypatch.setattr(scheduler, 'send_stock_notifications', capture)
    scheduler.run_monitor_job()
    output = capsys.readouterr().out
    for line in format_position(dict(position_status='HOLDING', **DETAILS), 525):
        assert line in output and line in messages[-1]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'stocks.db')
    database.create_tables()
    return database


@pytest.fixture
def client(db, monkeypatch):
    main = importlib.import_module('app.main')
    monkeypatch.setattr(main, 'start_scheduler', lambda: None)
    monkeypatch.setattr(main, 'stop_scheduler', lambda: None)
    with TestClient(main.app) as client:
        yield client


def test_create_default_and_both_update_directions(db):
    assert db.add_stock('2408', position_status='HOLDING')['position_status'] == 'HOLDING'
    assert db.add_stock('2454')['position_status'] == 'WATCHING'
    assert {row['stock_code']: row['position_status'] for row in db.get_all_stocks()} == {
        '2408': 'HOLDING', '2454': 'WATCHING'}
    for status, label in [('HOLDING', '已持有'), ('WATCHING', '觀察中')]:
        assert db.update_position_status('2454', status)['position_status'] == status
        assert db.get_stock('2454')['position_status'] == status
        assert format_position_status(db.get_stock('2454')['position_status']) == f'持倉狀態：{label}'
    assert db.update_position_status('missing', 'HOLDING') is None
    assert db.add_stock('2408') is None
    assert db.get_stock('2408')['position_status'] == 'HOLDING'


@pytest.mark.parametrize('invalid', ['BUY', 'SELL', 'ABC', '123', 123, '', None, 'holding'])
def test_invalid_write_rejected(db, client, invalid):
    db.add_stock('2454')
    with pytest.raises(ValueError):
        db.add_stock('2408', position_status=invalid)
    with pytest.raises(ValueError):
        db.update_position_status('2454', invalid)
    assert client.post('/watchlist', json={'stock_code': '2408', 'position_status': invalid}).status_code == 422
    assert client.patch('/watchlist/2454', json={'position_status': invalid}).status_code == 422
    assert db.get_stock('2408') is None
    assert db.get_stock('2454')['position_status'] == 'WATCHING'


def test_sql_constraint(db):
    db.add_stock('2454')
    with db.get_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE watchlist SET position_status = 'BUY'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('UPDATE watchlist SET position_status = NULL')
    connection.close()


def test_legacy_migration_preserves_every_existing_field(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.db'
    monkeypatch.setattr(database, 'DATABASE_PATH', path)
    with sqlite3.connect(path) as connection:
        connection.execute('''CREATE TABLE watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT NOT NULL UNIQUE,
            stock_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_signal TEXT, support_resistance_state TEXT)''')
        for code in ('2330', '2454', '2408', '3211'):
            connection.execute('INSERT INTO watchlist (stock_code, stock_name, last_signal, support_resistance_state) VALUES (?, ?, ?, ?)',
                               (code, '測試股票', '觀望', '{"keep": true}'))
        before = connection.execute('SELECT * FROM watchlist ORDER BY id').fetchall()
    connection.close()
    database.create_tables()
    database.create_tables()
    assert [row['position_status'] for row in database.get_all_stocks()] == ['WATCHING'] * 4
    with database.get_connection() as connection:
        after = connection.execute('SELECT id, stock_code, stock_name, created_at, last_signal, support_resistance_state FROM watchlist ORDER BY id').fetchall()
    connection.close()
    assert [tuple(row) for row in after] == before
    database.update_position_status('2408', 'HOLDING')
    database.create_tables()
    assert database.get_stock('2408')['position_status'] == 'HOLDING'


def test_api_create_read_update_and_missing(client):
    assert client.post('/watchlist', json={'stock_code': '2408', 'position_status': 'HOLDING'}).json()['position_status'] == 'HOLDING'
    assert client.post('/watchlist', json={'stock_code': '2454'}).json()['position_status'] == 'WATCHING'
    for status in ('HOLDING', 'WATCHING'):
        response = client.patch('/watchlist/2454', json={'position_status': status})
        assert response.status_code == 200
        assert response.json()['position_status'] == status
        assert client.get('/watchlist').json()[1]['position_status'] == status
    assert client.patch('/watchlist/missing', json={'position_status': 'HOLDING'}).status_code == 404
    assert client.patch('/watchlist/2454', json={}).status_code == 422
    routes = [(route.path, method) for route in client.app.routes for method in getattr(route, 'methods', [])]
    for method in ('POST', 'GET'):
        assert routes.count(('/watchlist', method)) == 1


@pytest.fixture
def market(monkeypatch):
    from app import stock
    frame = pd.DataFrame({'Open': [100 + i / 10 for i in range(90)],
                          'High': [102 + i / 10 for i in range(90)],
                          'Low': [99 + i / 10 for i in range(90)],
                          'Close': [101 + i / 10 for i in range(90)],
                          'Volume': [1000] * 90}, index=pd.date_range('2026-01-01', periods=90))
    class Ticker:
        def history(self, **kwargs):
            return frame.copy()
    monkeypatch.setattr(stock, 'resolve_yahoo_symbol', lambda code: code)
    monkeypatch.setattr(stock.yf, 'Ticker', lambda symbol: Ticker())
    monkeypatch.setattr(stock, 'get_realtime_price', lambda code: {})
    monkeypatch.setattr(stock, 'get_market_change_percent', lambda: 0)
    monkeypatch.setattr(stock, 'attach_bayesian_support', lambda *args: None)
    return stock, frame


@pytest.mark.parametrize('empty', [False, True])
def test_analysis_propagation_and_decision_unchanged(market, empty):
    stock, frame = market
    if empty:
        frame.drop(frame.index, inplace=True)
    holding = stock.analyze_watchlist([{'stock_code': '2408', 'position_status': 'HOLDING'}])[0]
    watching = stock.analyze_watchlist([{'stock_code': '2408'}])[0]
    assert holding['status'] == watching['status'] == 'success'
    for row, status in [(holding, 'HOLDING'), (watching, 'WATCHING')]:
        assert row['position_status'] == status
        assert row['data']['position_status'] == status
        assert row['data']['decision_context']['position_status'] == status
        assert row['data']['timeframe_analysis']['decision_context']['position_status'] == status
    for key in ('entry_action', 'holder_action', 'risk_gate', 'entry_score', 'risk_score'):
        assert holding['data']['trading_decision'][key] == watching['data']['trading_decision'][key]
    assert holding['data']['trading_decision']['decision'] in ('ADD', 'HOLD', 'REDUCE', 'EXIT')
    assert watching['data']['trading_decision']['decision'] in ('ENTER', 'WAIT')
    assert holding['data']['analysis'] == watching['data']['analysis']


def test_analysis_api_loads_position(client, market):
    client.post('/watchlist', json={'stock_code': '2408', 'position_status': 'HOLDING'})
    assert client.get('/stock/2408/analysis').json()['decision_context']['position_status'] == 'HOLDING'
    assert client.get('/monitor').json()['results'][0]['data']['position_status'] == 'HOLDING'


def test_position_does_not_change_market_fingerprint():
    from app.decision_engine import DecisionContext, DecisionEngine
    from app.decision_transition_analyzer import context_fingerprint
    watching = DecisionContext()
    holding = replace(watching, position_status=PositionStatus.HOLDING)
    assert context_fingerprint(watching) == context_fingerprint(holding)
    w, h = DecisionEngine().evaluate(watching), DecisionEngine().evaluate(holding)
    assert (w.entry_action, w.holder_action, w.risk_gate) == (h.entry_action, h.holder_action, h.risk_gate)
    assert w.decision == 'WAIT' and h.decision == 'HOLD'


def test_scheduler_terminal_and_discord_update(db, market, monkeypatch, capsys):
    from app import scheduler
    db.add_stock('2454', '聯發科')
    messages = []
    def capture(items):
        messages.extend(item['message'] for item in items)
        return {'matched_count': len(items), 'success_count': len(items), 'failed_count': 0,
                'failed_items': [], 'item_results': [dict(stock_code=item['stock_code'], success=True) for item in items]}
    monkeypatch.setattr(scheduler, 'send_stock_notifications', capture)
    for status, label in [('HOLDING', '已持有'), ('WATCHING', '觀察中')]:
        db.update_position_status('2454', status)
        # Force an ordinary technical notification in each run; no real webhook.
        with db.get_connection() as connection:
            connection.execute("UPDATE watchlist SET last_signal = NULL, last_notify_at = NULL, last_notification_signature = NULL")
        connection.close()
        scheduler.run_monitor_job()
        output = capsys.readouterr().out
        assert f'持倉狀態：{label}' in output
        assert messages and f'持倉狀態：{label}' in messages[-1]
        assert '【交易建議】' in output and '【交易建議】' in messages[-1]
        assert ('目前動作：續抱' if status == 'HOLDING' else '目前動作：等待') in messages[-1]
        assert '【操作參考】' not in messages[-1]
        assert 'position_status:' not in messages[-1]
