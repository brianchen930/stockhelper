"""Offline continuous two-year replay, strict snapshot merge and causal audits.

Run from project root: python -B -X utf8 -m 666_strategy.verification.verify_continuous_fugle
No production modules, strategy rules, prices or account credentials are changed.
"""
import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd

from ..backtest import BacktestConfig, export_backtest, fetch_backtest_data, prepare_data, run_backtest
from ..calendar import bundled_calendar
from ..fugle_provider import FugleSnapshotProvider, POLICY, SCHEMA, canonical_digest, native_frame
from .analyze_fugle_trades import analyze_fills, analyze_trades, summarize, write_csv
from .verify_fugle_integration import audit_result
from .verify_previous_fugle_year import equity_attribution, open_position_analysis, sha, write_json

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
ROOT = HERE/'fugle_continuous_20241030_20261029'
START, BOUNDARY, SECOND, END = '2024-10-30', '2025-10-29', '2025-10-30', '2026-10-29'
INPUTS = [HERE/'fugle_20241030_20251029'/'fugle_history_20241030_20251029.snapshot.json',
          HERE/'fugle_history_20251030_20261029.snapshot.json']
SNAPSHOT = ROOT/'fugle_history_20241030_20261029.snapshot.json'
SYMBOLS = ('2330', '2408')


def annual_path(symbol, early):
    return (HERE/'fugle_20241030_20251029'/f'fugle_backtest_{symbol}_20241030_20251029.json'
            if early else HERE/f'fugle_backtest_{symbol}_20251030_20261029.json')


def result_path(symbol):
    return ROOT/f'fugle_backtest_{symbol}_20241030_20261029.json'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def merge_snapshots():
    """Reject disagreements before de-duplication, including non-price source fields."""
    providers = [FugleSnapshotProvider(p) for p in INPUTS]
    docs = [read(p) for p in INPUTS]
    calendar, _ = bundled_calendar(date(2024, 5, 6), date(2026, 10, 29))
    for p in providers:
        assert tuple(day for day in calendar.sessions if p.calendar.start_date <= day <= p.calendar.through_date) == p.calendar.sessions
    overlap, sources = [], []
    for symbol in SYMBOLS:
        for interval in ('60m', '1d'):
            pair = symbol+'.TW', interval
            indices = [{r['bar_start']: r for item in p.snapshots
                        if (item['symbol'], item['interval']) == pair for r in item['rows']}
                       for p in providers]
            common = sorted(indices[0].keys() & indices[1].keys())
            differences = [dict(bar_start=at, early=indices[0][at], late=indices[1][at])
                           for at in common if indices[0][at] != indices[1][at]]
            overlap.append(dict(symbol=symbol, interval=interval, overlap_rows=len(common),
                first=common[0] if common else None, last=common[-1] if common else None,
                compared_fields=sorted(next(iter(indices[0].values())).keys()), differences=differences))
            write_json(ROOT/'overlap_validation.json', dict(series=overlap, passed=not any(x['differences'] for x in overlap)))
            if differences or not common:
                raise ValueError('Snapshot conflict or absent expected overlap; no backtest permitted')
            combined = indices[0] | indices[1]
            sources.append(dict(symbol=pair[0], interval=interval, rows=[combined[k] for k in sorted(combined)]))
    frames = {(i['symbol'], i['interval']): native_frame(i['rows'], i['interval']) for i in sources}
    coverage = []
    for symbol in SYMBOLS:
        data = prepare_data(frames[symbol+'.TW', '60m'], frames[symbol+'.TW', '1d'],
            symbol=symbol+'.TW', start=START, end=END, calendar=calendar, now=docs[1]['as_of'])
        assert len(data.window.sessions) == 484
        assert len(data.history.bars) == 2480 and len(data.daily.bars) == 604
        coverage.append(dict(symbol=symbol, passed=True, run_sessions=len(data.window.sessions),
            run_60m_bars=5*len(data.window.sessions), validated_60m=len(data.history.bars),
            validated_daily=len(data.daily.bars), minute_warmup_bars=60, daily_warmup_sessions=120,
            minute_start=data.window.minute_start.isoformat(), daily_start=data.window.daily_start.isoformat(),
            cutoff=data.window.cutoff.isoformat(), minute_issues=data.history.issues, daily_issues=data.daily.issues))
    inputs = [dict(file=str(p.relative_to(PROJECT)), sha256=sha(p), payload_sha256=d['payload_sha256'],
                   as_of=d['as_of'], original_provenance=d['provenance']) for p, d in zip(INPUTS, docs)]
    manifest = dict(mode='strict_union_of_two_verified_fugle_snapshots', inputs=inputs,
                    overlap=overlap, coverage=coverage, network_requests=0, synthesized_rows=0)
    write_json(ROOT/'merge_manifest.json', manifest)
    document = dict(schema=SCHEMA, policy=POLICY, as_of=docs[1]['as_of'],
        provenance=dict(source_file='merge_manifest.json', source_sha256=sha(ROOT/'merge_manifest.json'),
            received_at=max(d['provenance']['received_at'] for d in docs),
            inputs=inputs, requests=[], operation='Exact validated union; identical overlap de-duplicated'),
        calendar=dict(sessions=[str(d) for d in calendar.sessions], start_date=str(calendar.start_date),
                      through_date=str(calendar.through_date), source=calendar.source),
        source_snapshots=sources, validation=coverage)
    document['payload_sha256'] = canonical_digest(document)
    previous = SNAPSHOT.read_bytes() if SNAPSHOT.exists() else None
    write_json(SNAPSHOT, document)
    assert previous is None or previous == SNAPSHOT.read_bytes()
    FugleSnapshotProvider(SNAPSHOT)
    print('MERGE PASSED: identical overlaps; each symbol 2480 native 60m + 604 daily, 484 run sessions.', flush=True)
    return manifest


def run():
    provider = FugleSnapshotProvider(SNAPSHOT)
    for symbol in SYMBOLS:
        early, late = read(annual_path(symbol, True)), read(annual_path(symbol, False))
        assert early['config'] == late['config'] and early['config']['initial_cash'] == 1000000
        data = fetch_backtest_data(symbol, START, END, provider=provider)
        result = run_backtest(data, config=BacktestConfig(**early['config']))
        export_backtest(result, result_path(symbol))
        print(symbol+' CONTINUOUS '+json.dumps(result.summary), flush=True)


def compare_years(saved, early, late, data, provider):
    """Keep genuine annual resets separate from the actual continuous segments."""
    before = lambda rows, key: [r for r in rows if r[key][:10] <= BOUNDARY]
    assert before(saved['fills'], 'execution_time') == early['fills']
    assert before(saved['trades'], 'exit_time') == early['trades']
    assert before(saved['equity_curve'], 'at') == early['equity_curve']
    assert before(saved['raw_signals'], 'signal_time') == early['raw_signals']
    assert before(saved['events'], 'at') == early['events']
    boundary = before(saved['equity_curve'], 'at')[-1]
    assert boundary['equity'] == early['summary']['ending_equity']
    assert boundary['shares'] == early['open_position']['shares']
    late_data = fetch_backtest_data(saved['symbol'], SECOND, END, provider=provider)
    # Cross-year moving averages use all preceding observations without reinitialization.
    common = late_data.history.bars.index[60:]
    minute_error = float((data.history.bars.loc[common, 'MA60']-late_data.history.bars.loc[common, 'MA60']).abs().max())
    assert minute_error < 1e-9
    late_signals = [r for r in saved['raw_signals'] if r['signal_time'][:10] >= SECOND]
    full_keys = {(r['signal_time'], r['signal_type']) for r in late_signals}
    annual_keys = {(r['signal_time'], r['signal_type']) for r in late['raw_signals']}
    trend_diffs = []
    for event in saved['events']:
        if event['phase'] != 'CLOSE' or event['at'][:10] < SECOND:
            continue
        full, separate = data.daily.snapshot_at(event['at']), late_data.daily.snapshot_at(event['at'])
        if full['status'] != separate['status']:
            trend_diffs.append(dict(at=event['at'], full=full, separate=separate))
    assert not trend_diffs
    continuous_fills = [f for f in saved['fills'] if f['execution_time'][:10] >= SECOND]
    key = lambda f: (f['execution_time'], f['action'])
    continuous_map = {key(f): f for f in continuous_fills}
    separate_map = {key(f): f for f in late['fills']}
    shared = sorted(continuous_map.keys() & separate_map.keys())
    only_full = [continuous_map[k] for k in sorted(continuous_map.keys()-separate_map.keys())]
    only_annual = [separate_map[k] for k in sorted(separate_map.keys()-continuous_map.keys())]
    cross_year = [t for t in saved['trades'] if t['entry_time'][:10] <= BOUNDARY < t['exit_time'][:10]]
    early_costs = early['summary']['commissions']+early['summary']['transaction_taxes']
    full_costs = saved['summary']['commissions']+saved['summary']['transaction_taxes']
    segment_profit = round(saved['summary']['ending_equity']-boundary['equity'], 2)
    # A clearly labelled arithmetic comparator, NOT an executed portfolio or extra replay.
    # It assumes the independent late annual percentage scaled to boundary equity.
    scaled_annual_end = boundary['equity']*(1+late['summary']['total_return_percent']/100)
    return dict(early_annual_summary=early['summary'], late_annual_summary=late['summary'],
        early_annual_open_position=early['open_position'], early_annual_pending=early['pending_decision'],
        first_year_fills_trades_signals_events_equity_exact=True, boundary=boundary,
        cross_year_trades=cross_year, cross_year_open_at_end=(saved['open_position'] if saved['open_position']
            and saved['open_position']['entry_time'][:10] <= BOUNDARY else None),
        second_year_continuous=dict(initial_equity=boundary['equity'], ending_equity=saved['summary']['ending_equity'],
            net_profit=segment_profit, return_percent=segment_profit/boundary['equity']*100,
            commissions=round(saved['summary']['commissions']-early['summary']['commissions'], 2),
            transaction_taxes=round(saved['summary']['transaction_taxes']-early['summary']['transaction_taxes'], 2),
            fees_and_taxes=round(full_costs-early_costs, 2)),
        arithmetic_reset_flat_compound_comparator=dict(
            explanation='Boundary equity multiplied by independently reset-flat year-two return. Arithmetic only; no fractional-share simulation, no forced-sale costs, not actual continuous trading.',
            ending_equity=scaled_annual_end, actual_minus_comparator=saved['summary']['ending_equity']-scaled_annual_end),
        second_year_signals=dict(continuous=len(full_keys), independent=len(annual_keys),
            only_continuous=sorted(full_keys-annual_keys), only_independent=sorted(annual_keys-full_keys)),
        cross_year_indicators=dict(maximum_minute_ma60_abs_difference=minute_error, daily_trend_status_differences=trend_diffs),
        fills_comparison=dict(common_count=len(shared), only_continuous=only_full, only_independent=only_annual,
            common_fills=[dict(time=k[0], action=k[1], continuous=continuous_map[k], independent=separate_map[k]) for k in shared]))


def audit():
    provider = FugleSnapshotProvider(SNAPSHOT)
    output = dict(period=[START, END], initial_cash_per_symbol=1000000, combined_portfolio=False,
                  snapshot_sha256=sha(SNAPSHOT), symbols={})
    for symbol in SYMBOLS:
        print(symbol+': independent costs, equity, signal and causal-prefix audits...', flush=True)
        data = fetch_backtest_data(symbol, START, END, provider=provider)
        saved = read(result_path(symbol))
        # Replay from the sealed artifact and compare ALL output fields, including pending state.
        result = run_backtest(data, config=BacktestConfig(**saved['config']))
        assert result.to_dict() == saved
        independent = audit_result(data, result, provider)
        fills, raw = analyze_fills(saved, data)
        timing = []
        for f in fills:
            si = data.history.bars.index.get_loc(pd.Timestamp(f['signal_bar_start']))
            ei = data.history.bars.index.get_loc(pd.Timestamp(f['execution_time']))
            assert ei == si+1
            assert pd.Timestamp(f['signal_daily_close_time']) <= pd.Timestamp(f['trigger_time'])
            timing.append(dict(**f, next_bar_index_difference=ei-si, next_bar_passed=True,
                daily_close_known_at_trigger=True, signal_complete_before_execution=True,
                overnight=pd.Timestamp(f['signal_bar_start']).date() != pd.Timestamp(f['execution_time']).date()))
        trades = analyze_trades(saved, data, fills)
        stats = summarize(saved, data, trades, fills, raw)
        assert stats['all_trades']['net_profit'] == saved['summary']['realized_net_profit']
        for distribution in (stats['holding']['calendar_day_distribution'], stats['holding']['session_distribution']):
            assert sum(r['count'] for r in distribution) == len(trades)
        opened = open_position_analysis(saved, data)
        if opened:
            stats['holding']['market_exposure_percent'] = (
                sum(t['holding_market_minutes'] for t in trades)+opened['holding_market_minutes'])/(len(data.window.sessions)*270)*100
        attribution = equity_attribution(saved, trades)
        write_csv(ROOT/f'{symbol}_equity_attribution.csv', attribution.pop('ledger'))
        write_csv(ROOT/f'{symbol}_fill_timing_and_triggers.csv', timing)
        write_csv(ROOT/f'{symbol}_trade_diagnostics.csv', trades)
        write_csv(ROOT/f'{symbol}_raw_signal_outcomes.csv', raw)
        early, late = read(annual_path(symbol, True)), read(annual_path(symbol, False))
        comparison = compare_years(saved, early, late, data, provider)
        output['symbols'][symbol] = dict(summary=saved['summary'], config=saved['config'],
            benchmark=saved['benchmark'], statistics=stats, open_position=opened, equity=attribution,
            trades=trades, comparison=comparison, independent_audit=independent,
            exact_replay_passed=True, timing_audit=dict(passed=True, fills_checked=len(timing),
                overnight_fills=sum(r['overnight'] for r in timing), same_timestamp_next_bar_valid=True),
            annual_input_sha256={str(early_flag):sha(annual_path(symbol, early_flag)) for early_flag in (True, False)},
            output_sha256=sha(result_path(symbol)))
        write_json(ROOT/'comparison.json', output)
        print(symbol+': all audits passed.', flush=True)
    return output


def protected_validation():
    baseline = read(ROOT/'protected_baseline.json')
    changed = [p for p, expected in baseline.items() if not (PROJECT/p).is_file() or sha(PROJECT/p) != expected]
    assert not changed, changed
    return dict(passed=True, existing_files_checked=len(baseline), changed_existing_files=changed,
                strategy_rules_unchanged=True, providers_unchanged=True, original_app_unchanged=True)


def annual_indicator_parity():
    """Compare actual saved annual decisions, not only a freshly prepared window."""
    checks = {}
    for symbol in SYMBOLS:
        full, late = read(result_path(symbol)), read(annual_path(symbol, False))
        continuous = {e['at']:e for e in full['events'] if e['phase']=='CLOSE' and e['at'][:10]>=SECOND}
        separate = {e['at']:e for e in late['events'] if e['phase']=='CLOSE'}
        assert continuous.keys() == separate.keys()
        errors = {field:max(abs(continuous[t]['trend'][field]-separate[t]['trend'][field]) for t in separate)
                  for field in ('close','daily_ma60','daily_ma120','daily_ma60_5_sessions_ago')}
        assert all(value < 1e-9 for value in errors.values())
        assert all(continuous[t]['trend']['status']==separate[t]['trend']['status'] for t in separate)
        fc = {(f['execution_time'],f['action']):f for f in full['fills'] if f['execution_time'][:10]>=SECOND}
        fa = {(f['execution_time'],f['action']):f for f in late['fills']}
        assert not (fa.keys()-fc.keys())
        assert all(fc[k]['price']==fa[k]['price'] for k in fa)
        extra = [fc[k] for k in fc.keys()-fa.keys()]
        assert len(extra)==1 and extra[0]['action']=='SELL'
        cross = next(t for t in full['trades'] if t['trade_id']==extra[0]['trade_id'])
        assert cross['entry_time'][:10] <= BOUNDARY < cross['exit_time'][:10]
        checks[symbol] = dict(passed=True, actual_saved_independent_annual_compared=True,
            daily_close_events_compared=len(separate), maximum_daily_value_differences=errors,
            common_execution_prices_identical=True, only_extra_fill_is_cross_year_sell=True)
    write_json(ROOT/'annual_indicator_parity.json', checks)
    return checks


def report():
    artifact = read(ROOT/'comparison.json')
    assert set(artifact['symbols']) == set(SYMBOLS), 'Both complete symbol audits are required'
    assert artifact['period'] == [START, END]
    merge = read(ROOT/'merge_manifest.json')
    protected = protected_validation()
    parity = annual_indicator_parity()
    tests_path = ROOT/'tests_result.json'
    tests = read(tests_path) if tests_path.exists() else dict(status='NOT_RUN')
    validation = dict(status='PASSED' if tests.get('exit_code') == 0 else 'AUDITS_PASSED_TESTS_PENDING',
        period=[START, END], missing_data=[], conflicts=[], full_coverage=merge['coverage'],
        overlap=merge['overlap'], protected_files=protected, tests=tests, annual_indicator_parity=parity,
        snapshot_sha256=sha(SNAPSHOT), network_requests=0, synthesized_rows=0,
        verification_script_sha256=sha(Path(__file__)),
        output_sha256={p.name:sha(p) for p in sorted(ROOT.glob('*.csv'))},
        audits={s:dict(independent=v['independent_audit'], exact_replay=v['exact_replay_passed'],
                       timing=v['timing_audit']) for s, v in artifact['symbols'].items()})
    write_json(ROOT/'validation.json', validation)
    lines = ['# 666_strategy 連續兩年 Fugle 回測與年度比較', '',
        f'期間：**{START}～{END}**。每檔各自以 **1,000,000 元**開始，並非兩檔共用資金的組合。'
        '兩年以單次 run_backtest 連續運行，跨年不重設現金、股數、待執行決策或均線歷史。', '',
        '## 資料與設定', '',
        '- 完全離線重用兩份既有真實 Fugle 快照；本次下載 0 次、補造行情 0 筆、未縮短期間。',
        '- 每檔 484 個交易日、2,420 根期間內原生 60 分 K，另有 60 根暖身，共 2,480 根；日 K 含 120 日暖身共 604 筆。',
        '- 60 分暖身從 2024-10-14 09:00、日 K 暖身從 2024-05-06 開始。每年使用既有官方交易日曆，所有缺漏／重複／無效值均由原完整性檢查阻擋。',
        '- 每檔重疊 60 分 K 60 筆、日 K 120 筆，所有保存欄位完全一致後才去重合併。2025-10-29 接續 2025-10-30，未插入額外暖身或重設 MA60。',
        '- Asia/Taipei，成交量統一為股；保留來源張／股及時間欄位。每天 09、10、11、12、13 時五根 K；13:00 K 至 13:30 完成，沿用已驗證含收盤集合競價的 Fugle 原生值。',
        '- 原 BUY／SELL／EXIT、日線多頭過濾、下一根 K 的 Open 全數保留；每次以可用現金買入整股。手續費 0.1425%、最低 20 元，賣出交易稅 0.3%；滑價 0，未計股利。',
        '- 期末未強制平倉；勝率與持倉分布以已完成來回交易計算。最大回撤包含初始本金及每根 Open 成交後／完成 Close 的淨值，不推測棒內 High／Low 路徑。', '',
        f"合併快照 SHA-256：`{artifact['snapshot_sha256']}`。來源檔、原始取得時間、SHA-256、逐欄重疊核對與完整性證據見 [merge_manifest.json](merge_manifest.json)。", '',
        '## 兩年績效', '',
        '| 股票 | 期末權益 | 淨損益 | 總報酬率 | 最大回撤 | 完成交易／勝 | 勝率 | 手續費 | 交易稅 | 買進持有報酬 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    names = {'2330':'台積電', '2408':'南亞科'}
    for symbol, v in artifact['symbols'].items():
        s = v['summary']
        lines.append(f"| {symbol} {names[symbol]} | {s['ending_equity']:,.2f} | {s['total_net_profit']:,.2f} | {s['total_return_percent']:.6f}% | {s['max_drawdown_percent']:.6f}% | {s['completed_trades']}／{s['winning_trades']} | {s['win_rate_percent']:.4f}% | {s['commissions']:,.2f} | {s['transaction_taxes']:,.2f} | {s['buy_and_hold_return_percent']:.6f}% |")
    lines += ['', '買進持有：期間首根 Open 買入可負擔整股、計入買入手續費，持有至期末 Close 評價，不虛設賣出費稅。'
        '同樣以單一 100 萬元運行兩年；各年度基準的起買價不同，不可直接相加。', '', '## 連續運行與獨立年度', '',
        '| 股票 | 第一年獨立報酬 | 第二年獨立報酬（重設 100 萬、空手） | 連續第二年起始權益 | 連續第二年損益 | 連續第二年區段報酬 |',
        '|---|---:|---:|---:|---:|---:|']
    for symbol, v in artifact['symbols'].items():
        c = v['comparison']; seg = c['second_year_continuous']
        lines.append(f"| {symbol} | {c['early_annual_summary']['total_return_percent']:.6f}% | {c['late_annual_summary']['total_return_percent']:.6f}% | {seg['initial_equity']:,.2f} | {seg['net_profit']:,.2f} | {seg['return_percent']:.6f}% |")
    lines += ['', '兩次獨立年度都在年初回到 100 萬且空手，第一年期末持股僅評價，第二年也沒有承接它；'
        '因此年度報酬相加或相乘都不是實際連續回測。連續第二年報酬以 2025-10-29 收盤權益作分母。'
        '完整連續回測在第一年度截止的逐筆成交、完成交易、原始訊號、事件與淨值曲線均與既有第一年結果完全相同。']
    lines += ['', '| 股票 | 獨立年度 | 期末權益 | 完成交易／勝 | 勝率 | 最大回撤 | 費稅合計 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for symbol, v in artifact['symbols'].items():
        for label, key in [('2024-10-30～2025-10-29', 'early_annual_summary'),
                           ('2025-10-30～2026-10-29', 'late_annual_summary')]:
            a = v['comparison'][key]
            lines.append(f"| {symbol} | {label} | {a['ending_equity']:,.2f} | {a['completed_trades']}／{a['winning_trades']} | "
                f"{a['win_rate_percent']:.4f}% | {a['max_drawdown_percent']:.6f}% | {a['commissions']+a['transaction_taxes']:,.2f} |")
    for symbol, v in artifact['symbols'].items():
        s, c, stats = v['summary'], v['comparison'], v['statistics']
        p = c['early_annual_open_position']; a = c['early_annual_summary']; b = c['late_annual_summary']; seg = c['second_year_continuous']
        lines += ['', f'### {symbol} {names[symbol]}：跨年持倉與資金累積', '',
            f"2025-10-29 收盤現金 {a['ending_cash']:,.2f} 元，承接 {p['shares']:,} 股（{p['entry_time']} 以 {p['entry_price']:g} 元買入），"
            f"市值 {p['market_value']:,.2f} 元，未實現損益 {p['unrealized_net_profit']:,.2f} 元。跨年待執行決策：`{json.dumps(c['early_annual_pending'], ensure_ascii=False)}`。"]
        for t in c['cross_year_trades']:
            exitfill = next(f for f in read(result_path(symbol))['fills'] if f['action'] != 'BUY' and f['trade_id'] == t['trade_id'])
            lines.append(f"跨年交易 #{t['trade_id']}：{t['entry_time']} 買進 {t['shares']:,} 股 @ {t['entry_price']:g}，"
                f"於 {t['exit_time']} {t['exit_action']} @ {t['exit_price']:g}，完整交易淨損益 {t['net_profit']:,.2f} 元。"
                f"觸發確認時間 {exitfill['signal_time'] or exitfill['trend_close_time']}，依原次棒 Open 成交。"
                '交易損益包含跨年前累積；第二年區段損益已扣除跨年前的評價，不能重複加總。')
            incremental = round(t['net_profit']-p['unrealized_net_profit'], 2)
            lines.append(f"此持倉從跨年評價到賣出的第二年新增損益為 {incremental:,.2f} 元，"
                f"賣出後現金 {exitfill['cash_after']:,.2f} 元；原先已列在第一年的未實現損益 "
                f"{p['unrealized_net_profit']:,.2f} 元不再計入第二年。"
                f"其餘第二年交易合計新增淨損益 {seg['net_profit']-incremental:,.2f} 元，"
                f"兩部分相加為第二年區段淨損益 {seg['net_profit']:,.2f} 元。")
        fc = c['fills_comparison']; signals = c['second_year_signals']
        lines += [f"第二年度原始交叉訊號：連續 {signals['continuous']} 個、獨立 {signals['independent']} 個；"
            f"僅連續 {len(signals['only_continuous'])} 個、僅獨立 {len(signals['only_independent'])} 個。"
            f"MA60 最大絕對差 {c['cross_year_indicators']['maximum_minute_ma60_abs_difference']:.3g}；日線多頭狀態差異 0。",
            f"實際成交：共同時點／動作 {fc['common_count']} 筆，連續額外 {len(fc['only_continuous'])} 筆，獨立額外 {len(fc['only_independent'])} 筆。"
            '原始市場訊號與持倉狀態是不同層次；跨年既有持股會執行賣出，空手年度則不會。']
        if fc['only_continuous'] or fc['only_independent']:
            lines += ['', '| 僅出現於 | 成交時間 | 動作 | 價格 | 股數 |', '|---|---|---|---:|---:|']
            for origin, rows in [('連續', fc['only_continuous']), ('獨立年度', fc['only_independent'])]:
                for f in rows:
                    lines.append(f"| {origin} | {f['execution_time']} | {f['action']} | {f['price']:g} | {f['shares']:,} |")
            lines.append('')
        if fc['common_fills']:
            firstbuy = next((f for f in fc['common_fills'] if f['action'] == 'BUY'), None)
            if firstbuy:
                f, g = firstbuy['continuous'], firstbuy['independent']
                lines.append(f"共同進場案例：{firstbuy['time']} 同以 {f['price']:g} 元買進，連續資金買 {f['shares']:,} 股，"
                    f"獨立年度買 {g['shares']:,} 股；之後按各自現金及費稅繼續累積，未固定股數或重設本金。")
        lines += [f"第二年連續手續費 {seg['commissions']:,.2f}、交易稅 {seg['transaction_taxes']:,.2f}，"
            f"合計 {seg['fees_and_taxes']:,.2f} 元；獨立第二年費稅 {b['commissions']+b['transaction_taxes']:,.2f} 元。"
            '絕對費稅會隨承接的資金及交易數改變，不能把增加的金額全部歸因於更多交易。']
        arithmetic = c['arithmetic_reset_flat_compound_comparator']
        lines += [f"僅作算術對照：若把獨立第二年報酬率套在跨年權益上，得 {arithmetic['ending_equity']:,.2f} 元；"
            f"實際連續期末相差 {arithmetic['actual_minus_comparator']:,.2f} 元。此數字沒有模擬整股、跨年清倉費稅或持倉，"
            '不能視為另一個可成交回測；差額反映承接持倉與成交路徑，也包含整股及費用取整效應。', '',
            f'### {symbol} 持倉、反覆進出與回撤', '',
            f"已完成交易平均持倉 {stats['holding']['mean_calendar_days']:.4f} 日，中位數 {stats['holding']['median_calendar_days']:.4f} 日，"
            f"最大 {stats['holding']['max_calendar_days']:.4f} 日；市場時間持股比率 {stats['holding']['market_exposure_percent']:.4f}%。",
            '', '| 持有日曆天數 | 筆數 | 勝率 | 淨損益 |', '|---|---:|---:|---:|']
        for r in stats['holding']['calendar_day_distribution']:
            rate = f"{r['win_rate_percent']:.2f}%" if r['count'] else '—'
            lines.append(f"| {r['calendar_days']} | {r['count']} | {rate} | {r['net_profit']:,.2f} |")
        lines += ['', '| 持股交易日數（不含出場棒） | 筆數 | 淨損益 |', '|---|---:|---:|']
        for r in stats['holding']['session_distribution']:
            lines.append(f"| {r['exposed_sessions']} | {r['count']} | {r['net_profit']:,.2f} |")
        g = stats['groups']; perf = stats['all_trades']
        lines += ['', f"當日買賣 {g['same_calendar_day']['count']} 筆；持股不超過 3 個交易日 {g['up_to_3_exposed_sessions']['count']} 筆，"
            f"淨損益 {g['up_to_3_exposed_sessions']['net_profit']:,.2f} 元；距前次出場不超過 3 個交易日再次進場 "
            f"{g['rapid_reentry_within_3_session_gaps']['count']} 筆，淨損益 {g['rapid_reentry_within_3_session_gaps']['net_profit']:,.2f} 元。"
            '這些群組有交集，不能相加；短期定義固定沿用既有分析。',
            f"平均獲利 {perf['average_win']:,.2f} 元、平均虧損 {perf['average_loss']:,.2f} 元；"
            f"毛價差損益 {perf['gross_price_profit']:,.2f} 元，扣已完成交易費稅 {perf['total_costs']:,.2f} 元。"
            f"曾有正浮盈最後淨虧 {g['positive_mark_profit_to_net_loss']['count']} 筆。"]
        if perf['gross_price_profit'] > 0 and perf['net_profit'] < 0:
            lines.append('兩年合計毛價差為正，扣除原設定費稅後轉為淨虧；成本對這條實際交易路徑具有決定性影響。')
        elif perf['gross_price_profit'] > 0:
            lines.append(f"費稅占毛價差獲利 {perf['total_costs']/perf['gross_price_profit']*100:.4f}%；"
                '整體獲利仍須與短期交易群組的淨損益及浮盈回吐一起判讀。')
        eq = v['equity']; best = stats['best_trade']; give = stats['largest_giveback_trade']
        lines += [f"最大回撤由 {eq['peak']['at']}（{eq['peak']['phase']}）權益 {eq['peak']['equity']:,.2f}，"
            f"降至 {eq['trough']['at']}（{eq['trough']['phase']}）{eq['trough']['equity']:,.2f} 元；"
            f"期末{'已' if eq['recovered_by_end'] else '未'}收復此高點。逐交易歸因見 equity_attribution CSV。",
            f"最大獲利交易 #{best['trade_id']}：{best['entry_time']}～{best['exit_time']}，淨利 {best['net_profit']:,.2f} 元。"
            f"最大浮盈回吐交易 #{give['trade_id']}：觀察到的最高浮盈 {give['peak_mark_profit']:,.2f} 元，"
            f"最終淨損益 {give['net_profit']:,.2f} 元，回吐 {give['peak_to_realized_giveback']:,.2f} 元。"
            '浮盈以實際持有期間的 Open／完成 Close 及出場 Open 觀察，不假設能在 High 出場。', '',
            f'### {symbol} 完整交易紀錄', '',
            f'[逐筆成交與費稅](fugle_backtest_{symbol}_20241030_20261029.fills.csv) · '
            f'[完整來回交易](fugle_backtest_{symbol}_20241030_20261029.trades.csv) · '
            f'[逐筆觸發與時間核對]({symbol}_fill_timing_and_triggers.csv) · '
            f'[持倉／再進場／浮盈診斷]({symbol}_trade_diagnostics.csv)', '',
            '| # | 買進時間 | 出場時間／動作 | 買價 | 賣價 | 股數 | 淨損益 |', '|---:|---|---|---:|---:|---:|---:|']
        for t in v['trades']:
            lines.append(f"| {t['trade_id']} | {t['entry_time'][:16]} | {t['exit_time'][:16]} {t['exit_action']} | {t['entry_price']:g} | {t['exit_price']:g} | {t['shares']:,} | {t['net_profit']:,.2f} |")
        lines += ['', '期末持倉：'+('無。' if v['open_position'] is None else json.dumps(v['open_position'], ensure_ascii=False))]
    lines += ['', '## 時間與前視偏差驗證', '',
        '逐筆驗證訊號所屬棒已完成、確認時間不晚於成交、成交棒索引恰為訊號棒加一、成交價等於該棒 Open，'
        '且日線判斷只使用確認當下已完成的日 K。盤中前棒 Close 與次棒 Open 同為整點是事件順序相鄰，並非使用成交棒的 Close。'
        '13:00 棒的 13:30 確認只可在次交易日 09:00 成交。EXIT 另查日線非多頭原因及其實際確認棒。',
        '以獨立加總核對 MA60 與原始交叉；以 Decimal 重算成交、手續費、交易稅、現金、來回淨損益與基準整股；'
        '用獨立累積高點核對回撤。另以真實前綴重新跑 2024-10-30、2025-10-29、2026-10-28，'
        '所有先前成交／已完成交易必須與完整回測完全相同，確保加入未來資料未回寫歷史。', '',
        '| 股票 | 成交時間檢查筆數 | 跨日成交 | 完整結果精確重播 | 前綴核對 |', '|---|---:|---:|---|---|']
    for symbol, v in artifact['symbols'].items():
        lines.append(f"| {symbol} | {v['timing_audit']['fills_checked']} | {v['timing_audit']['overnight_fills']} | 通過 | {', '.join(v['independent_audit']['prefix_checks'])} |")
    lines += ['', f"既有檔案 SHA-256 保護檢查：{protected['existing_files_checked']} 檔全部相同，未修改策略、Fugle／Yahoo Provider、原助手或既有回測功能。",
        f"另逐一比對既有獨立第二年保存的 1,205 個收棒日線判斷：狀態全部相同，日線數值最大差 "
        f"{max(error for v in parity.values() for error in v['maximum_daily_value_differences'].values()):.6g}"
        '（浮點累加尾差），未改變任何日線判斷；所有共同成交價格完全一致。'
        '明細見 [annual_indicator_parity.json](annual_indicator_parity.json)。',
        f"既有測試：{tests.get('summary', '尚未執行')}；完整紀錄 [tests.log](tests.log)。",
        '[機器可讀驗證結果](validation.json) · [完整比較與統計](comparison.json) · [合併快照](fugle_history_20241030_20261029.snapshot.json)', '',
        '可重現指令（在台股目錄）：`python -B -X utf8 -m 666_strategy.verification.verify_continuous_fugle`。'
        '也可用 `--stage merge|run|audit|report` 分階段執行；任何來源衝突或完整性錯誤均立即失敗並保存 failure.json，不產生縮短期間的結果。', '',
        '既有測試重跑：`python -B -X utf8 -m unittest discover -s 666_strategy/tests -v`。', '',
        '## 解讀界線', '',
        '跨年承接趨勢持倉及其資金累積會大幅影響兩年結果；必須同時看勝率、回撤、持倉長度和基準，'
        '不能將單一年度重設資金的表現當成連續資金績效。短期反覆進出、浮盈回吐和成本統計是對原策略路徑的描述，'
        '本次未改規則、參數或成交機制，未接入券商下單。']
    (ROOT/'COMPARISON_REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print('Comparison report and validation saved; protected existing files unchanged.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('all', 'merge', 'run', 'audit', 'report'), default='all')
    args = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    try:
        if args.stage in ('all', 'merge'):
            merge_snapshots()
        if args.stage in ('all', 'run'):
            run()
        if args.stage in ('all', 'audit'):
            audit()
        if args.stage in ('all', 'report'):
            report()
    except Exception as error:
        write_json(ROOT/'failure.json', dict(stage=args.stage, error_type=type(error).__name__,
            detail=str(error), period=[START, END], fabricated=False, shortened=False))
        raise


if __name__ == '__main__':
    main()
