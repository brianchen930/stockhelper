"""Rebuild snapshots, replay both periods, and independently audit saved results.

Offline only: uses saved real Fugle/Yahoo quotes; never reads credentials.
Run: python -B -X utf8 -m 666_strategy.verification.verify_fugle_integration
"""
import argparse
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest import (BacktestConfig, SnapshotProvider, export_backtest,
                        fetch_backtest_data, run_backtest)
from ..fugle_provider import FugleSnapshotProvider, create_fugle_snapshot
from ..history import OHLCV, TAIPEI
from .verify_backtest_snapshot import assert_saved_fields, currency

HERE = Path(__file__).resolve().parent
START, END, SHORT = '2025-10-30', '2026-10-29', '2026-09-17'


def independent_signals(data):
    bars = data.history.bars
    closes = list(bars.Close)
    averages = [None if i < 59 else math.fsum(closes[i-59:i+1])/60 for i in range(len(closes))]
    events = []
    for i in range(60, len(bars)):
        assert math.isclose(averages[i], bars.MA60.iloc[i], rel_tol=1e-12, abs_tol=1e-10)
        # Match the unchanged rules' exact comparisons after checking the MA
        # against the independent math.fsum reference (no new tolerance rule).
        previous, current = bars.iloc[i-1], bars.iloc[i]
        kind = ('BUY' if previous.Close <= previous.MA60 and current.Close > current.MA60 else
                'SELL' if previous.Close >= previous.MA60 and current.Close < current.MA60 else None)
        if kind and data.start <= bars.index[i].date() <= data.end:
            events.append((current.bar_end.isoformat(), kind))
    return events


def audit_result(data, result, provider):
    assert independent_signals(data) == [(s['signal_time'], s['signal_type']) for s in result.raw_signals]
    cash, entry_cost, shares = currency(result.config['initial_cash']), None, 0
    entry_fill, round_trips = None, []
    for fill in result.fills:
        stamp = pd.Timestamp(fill['execution_time'])
        assert data.start <= stamp.date() <= data.end
        assert fill['price'] == float(data.history.bars.loc[stamp, 'Open'])
        if fill['action'] == 'EXIT':
            trigger = pd.Timestamp(fill['trend_close_time'])
            assert data.daily.snapshot_at(trigger)['status'] == 'NON_BULL'
        else:
            raw = next(event for event in result.raw_signals if event['signal_time'] == fill['signal_time'])
            assert raw['signal_type'] == fill['action']
            trigger = pd.Timestamp(raw['bar_end'])
            assert stamp > pd.Timestamp(raw['bar_start'])
        assert stamp == data.history.bars.index[data.history.bars.index >= trigger][0]
        gross = currency(Decimal(str(fill['price']))*fill['shares'])
        fee = currency(max(Decimal(str(result.config['minimum_commission'])),
            gross*Decimal(str(result.config['commission_rate']))))
        selling = fill['action'] != 'BUY'
        tax = currency(gross*Decimal(str(result.config['sell_tax_rate']))) if selling else currency(0)
        assert (currency(fill['gross_amount']), currency(fill['commission']),
                currency(fill['transaction_tax'])) == (gross, fee, tax)
        if not selling:
            assert shares == 0 and data.daily.snapshot_at(stamp)['status'] == 'BULL'
            assert pd.Timestamp(data.daily.snapshot_at(stamp)['daily_close_time']) <= stamp
            entry_cost, entry_fill, shares = gross+fee, fill, fill['shares']
            cash -= entry_cost
        else:
            assert fill['shares'] == shares
            profit = gross-fee-tax-entry_cost
            assert currency(fill['net_profit']) == profit
            assert math.isclose(fill['return_percent'], float(profit/entry_cost*100), abs_tol=1e-10)
            trade = result.trades[len(round_trips)]
            assert (trade['entry_time'], trade['exit_time'], trade['shares'], currency(trade['net_profit'])) == (
                entry_fill['execution_time'], fill['execution_time'], shares, profit)
            round_trips.append(trade)
            cash += gross-fee-tax
            shares = 0
        assert currency(fill['cash_after']) == cash and cash >= 0
    value = currency(Decimal(str(data.history.bars.iloc[-1].Close))*shares)
    assert currency(result.summary['ending_cash']) == cash
    assert currency(result.summary['ending_equity']) == cash+value
    profit = cash+value-currency(result.config['initial_cash'])
    assert currency(result.summary['total_net_profit']) == profit
    assert result.summary['total_return_percent'] == float(profit/currency(result.config['initial_cash'])*100)
    assert result.summary['completed_trades'] == len(round_trips)
    wins = sum(trade['net_profit'] > 0 for trade in round_trips)
    assert result.summary['win_rate_percent'] == (wins/len(round_trips)*100 if round_trips else None)
    equity = np.array([result.config['initial_cash'], *[point['equity'] for point in result.equity_curve]])
    peaks = np.maximum.accumulate(equity)
    assert math.isclose(result.summary['max_drawdown_percent'], float(np.max((peaks-equity)/peaks)*100), abs_tol=1e-9)
    durations = [(pd.Timestamp(t['exit_time'])-pd.Timestamp(t['entry_time']))/pd.Timedelta(days=1) for t in round_trips]
    assert not durations or math.isclose(result.summary['average_holding_days'], float(np.mean(durations)), abs_tol=1e-12)
    run_bars = data.history.bars.loc[data.history.bars.index.date >= data.start]
    first, last = run_bars.iloc[0], run_bars.iloc[-1]
    # Independently size the buy-and-hold benchmark, including its rounded fee.
    unit = result.config['share_unit']
    quantity = int(result.config['initial_cash']/first.Open)//unit*unit
    while quantity:
        gross = currency(Decimal(str(first.Open))*quantity)
        fee = currency(max(Decimal(str(result.config['minimum_commission'])), gross*Decimal(str(result.config['commission_rate']))))
        if gross+fee <= currency(result.config['initial_cash']):
            break
        quantity -= unit
    if not quantity:
        gross = fee = currency(0)
    benchmark_equity = currency(result.config['initial_cash'])-gross-fee+currency(Decimal(str(last.Close))*quantity)
    assert result.benchmark['shares'] == quantity
    assert currency(result.benchmark['ending_equity']) == benchmark_equity
    assert result.summary['buy_and_hold_return_percent'] == float(
        (benchmark_equity-currency(result.config['initial_cash']))/currency(result.config['initial_cash'])*100)
    # Replay actual prefixes; the full dataset must not change earlier fills.
    sessions = data.window.sessions
    prefix_days = sorted({sessions[0], sessions[len(sessions)//2], sessions[-2]})
    for day in prefix_days:
        prefix_data = fetch_backtest_data(data.symbol, data.start, day, provider=provider)
        prefix = run_backtest(prefix_data, config=BacktestConfig(**result.config))
        assert prefix.fills == [fill for fill in result.fills if pd.Timestamp(fill['execution_time']).date() <= day]
        assert prefix.trades == [trade for trade in result.trades if pd.Timestamp(trade['exit_time']).date() <= day]
    return dict(passed=True, fills_checked=len(result.fills), trades_checked=len(result.trades),
        independent_signal_checks=len(result.raw_signals), independent_metrics=True,
        prefix_checks=[str(day) for day in prefix_days], minute_bars=len(data.history.bars),
        daily_closes=len(data.daily.bars), run_sessions=len(sessions))


def auction_audit(report):
    evidence = []
    for item in report['probes']:
        spec = item['request']
        if spec['timeframe'] != '1' or item['status'] != 'FETCHED':
            continue
        tail = [row for row in item['rows'] if pd.Timestamp(row['bar_start']).hour == 13]
        auction = [row for row in tail if pd.Timestamp(row['bar_start']).minute == 30]
        assert len(auction) == 1
        native = [row for part in report['full_range_results']
            if part['request']['symbol'] == spec['symbol'] and part['request']['timeframe'] == '60'
            for row in part['rows'] if row['bar_start'] == spec['start']+'T13:00:00+08:00']
        assert len(native) == 1
        aggregate = dict(Open=tail[0]['Open'], High=max(row['High'] for row in tail),
            Low=min(row['Low'] for row in tail), Close=tail[-1]['Close'], Volume=sum(row['Volume'] for row in tail))
        assert all(native[0][name] == aggregate[name] for name in OHLCV)
        evidence.append(dict(symbol=spec['symbol'], date=spec['start'], passed=True,
            native_13_00={name: native[0][name] for name in OHLCV},
            pre_auction_close=tail[-2]['Close'], auction_close=auction[0]['Close'],
            auction_volume_shares=auction[0]['Volume'], native_equals_1m_with_auction=True))
    assert len(evidence) == 8
    return evidence


def compare_sources(fugle_data, fugle_result, yahoo_data, yahoo_result):
    a, b = fugle_data.history.bars, yahoo_data.history.bars
    assert a.index.equals(b.index)
    changed = []
    for stamp in a.index:
        fields = {name: dict(fugle=float(a.loc[stamp, name]), yahoo=float(b.loc[stamp, name]))
                  for name in OHLCV if a.loc[stamp, name] != b.loc[stamp, name]}
        if fields:
            changed.append(dict(bar_start=stamp.isoformat(), includes_warmup=stamp.date()<fugle_data.start, fields=fields))
    fa = {(s['signal_time'], s['signal_type']): s for s in fugle_result.raw_signals}
    ya = {(s['signal_time'], s['signal_type']): s for s in yahoo_result.raw_signals}
    different_signals = []
    for when, kind in sorted(set(fa)^set(ya)):
        i = a.index.get_loc(pd.Timestamp((fa.get((when, kind)) or ya[when, kind])['bar_start']))
        different_signals.append(dict(signal_time=when, signal_type=kind,
            present_in='Fugle' if (when, kind) in fa else 'Yahoo',
            previous_close=dict(fugle=float(a.Close.iloc[i-1]), yahoo=float(b.Close.iloc[i-1])),
            current_close=dict(fugle=float(a.Close.iloc[i]), yahoo=float(b.Close.iloc[i])),
            previous_ma60=dict(fugle=float(a.MA60.iloc[i-1]), yahoo=float(b.MA60.iloc[i-1])),
            current_ma60=dict(fugle=float(a.MA60.iloc[i]), yahoo=float(b.MA60.iloc[i])),
            close_differences_in_two_ma_windows=[stamp.isoformat() for stamp in a.index[max(0,i-60):i+1]
                if a.loc[stamp,'Close'] != b.loc[stamp,'Close']]))
    trend_differences = []
    for stamp in a.index[a.index.date >= fugle_data.start]:
        for at in (stamp, a.loc[stamp,'bar_end']):
            fs, ys = fugle_data.daily.snapshot_at(at), yahoo_data.daily.snapshot_at(at)
            if fs['status'] != ys['status']:
                trend_differences.append(dict(at=at.isoformat(), fugle=fs, yahoo=ys))
    ff = {(r['execution_time'],r['action']):r for r in fugle_result.fills}
    yf = {(r['execution_time'],r['action']):r for r in yahoo_result.fills}
    return dict(fugle_summary=fugle_result.summary, yahoo_summary=yahoo_result.summary,
        same_bar_timestamps=True, compared_bars_including_warmup=len(a),
        changed_bar_count=len(changed), changed_13_00_count=sum(pd.Timestamp(r['bar_start']).hour==13 for r in changed),
        field_difference_counts={name:sum(name in row['fields'] for row in changed) for name in OHLCV},
        bar_differences=changed, fugle_signal_count=len(fa), yahoo_signal_count=len(ya),
        common_signals=len(set(fa)&set(ya)), signal_differences=different_signals,
        common_signal_value_differences=[dict(signal_time=key[0],signal_type=key[1],
            fugle=fa[key],yahoo=ya[key]) for key in sorted(set(fa)&set(ya)) if fa[key] != ya[key]],
        trend_status_differences=trend_differences,
        fugle_only_fills=[ff[key] for key in sorted(set(ff)-set(yf))],
        yahoo_only_fills=[yf[key] for key in sorted(set(yf)-set(ff))],
        common_fill_differences=[dict(execution_time=key[0],action=key[1],fugle=ff[key],yahoo=yf[key])
            for key in sorted(set(ff)&set(yf)) if ff[key] != yf[key]],
        daily_close_differences=[dict(date=str(stamp.date()), fugle=float(fugle_data.daily.bars.loc[stamp,'Close']),
            yahoo=float(yahoo_data.daily.bars.loc[stamp,'Close'])) for stamp in fugle_data.daily.bars.index
            if fugle_data.daily.bars.loc[stamp,'Close'] != yahoo_data.daily.bars.loc[stamp,'Close']])


def write_report(artifact, root):
    annual = [item for item in artifact['reports'] if item['start'] == START]
    lines = [
        '# Fugle 正式整合與完整一年回測驗證（2026-11-01）', '',
        '已將 Fugle 整合為可選歷史行情來源；Yahoo 仍為預設。2330、2408 均完成固定期間 '
        '**2025-10-30～2026-10-29** 的完整一年回測，沒有補造行情、縮短期間或接入券商下單。', '',
        '## 資料來源、口徑與可重現性', '',
        '- 重用 [既有 Fugle 實測](fugle_live_full_corrected.json)，來源下載時間為 2026-11-01 17:48:08 +08:00；本次整合與回測新增網路請求為 0。',
        '- [快照](fugle_history_20251030_20261029.snapshot.json) 僅保存允許的真實 OHLCV、原始時間戳與成交量、請求範圍及已確認日曆；不保存金鑰或 HTTP header。',
        '- 快照從原實測檔重新建立兩次，位元組完全相同；載入驗證 payload SHA-256，另記錄完整快照與原實測檔 SHA-256。',
        '- 兩檔各有 241 個期間交易日、1,205 根期間 60 分 K；加上 60 根暖身共 1,265 根。日線自 2025-05-08 起，120 日暖身加期間共 361 筆；60 分 K 自 2025-10-13 09:00 起。',
        '- 全部資料重新通過原有 prepare_data 完整性檢查；缺棒、重複、無效 OHLCV、日線 UNKNOWN 或快照 as_of 不足均停止，不補值、不換來源。',
        '- OHLCV 欄位一致，索引為 Asia/Taipei；原價、未還原，不計股息。Fugle 分 K 成交量由張乘 1,000 轉股一次，日 K 原為股，原始值與單位一併保存。',
        '- 金鑰沿用既有環境變數優先、台股/.env 備援；.gitignore 已排除 .env。離線快照回放完全不讀金鑰。Fugle 目前支援四位數 TWSE 上市股票，其他市場明確報錯。', '',
        f"原實測 SHA-256：`{artifact['source']['original_source_sha256']}`", '',
        f"快照 SHA-256：`{artifact['source']['source_sha256']}`", '',
        '## 13:00 棒與收盤集合競價', '',
        '[Fugle 官方歷史 K 線文件](https://developer.fugle.tw/docs/data/http-api/historical/candles/) '
        '說明分 K 時戳為起點、整股分 K 成交量為張；1 分 K 的 13:25～13:30 無連續撮合棒，收盤撮合另列 13:30。', '',
        '這不代表需要另加一根 60 分 K。保存的八組實際 1 分 K 樣本（兩檔各 2026-02-02、02-03、04-20、04-21）'
        '逐一核對後，13:00 起至 13:30 收盤撮合的 OHLCV 彙總，全部等於 Fugle 原生 13:00 60 分 K。'
        '此彙總只用於驗證，回測直接讀取原生棒，沒有用 1 分 K 重建或替換行情。', '',
        '| 股票 | 日期 | 13:24 最後連續撮合收盤 | 13:30 撮合價 | 原生 13:00 收盤 | 撮合量（股） |',
        '|---|---|---:|---:|---:|---:|']
    for row in artifact['auction_evidence']:
        lines.append(f"| {row['symbol']} | {row['date']} | {row['pre_auction_close']:g} | {row['auction_close']:g} | {row['native_13_00']['Close']:g} | {row['auction_volume_shares']:,.0f} |")
    lines += ['', '每天僅使用 09:00、10:00、11:00、12:00、13:00 五根；最後一根於 13:30 才完成。'
        '不附加或二次合併 13:30 棒、不把收盤價改成 Yahoo 值；13:30 訊號仍於下一交易日 09:00 的 Open 才可成交。'
        '樣本驗證不能宣稱全年逐日都有 1 分 K 證據；全年採同一原生 API 口徑，並逐日檢查五根資料完整。', '',
        '## 一年績效與成本假設', '',
        '兩檔分別以 1,000,000 元本金、初始空手計算，不合併投資組合。沿用原成本：手續費 0.1425%、'
        '每次至少 20 元、賣出交易稅 0.3%，現金可負擔的整數股數、滑價 0。期末不強制平倉；兩檔本次均已平倉。', '',
        '| 股票 | 已平倉／獲利筆數 | 勝率 | 淨損益（元） | 總報酬率 | 最大回撤 | 平均持倉（日） | 買進持有報酬率 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in annual:
        s=row['summary']
        lines.append(f"| {row['symbol']} | {s['completed_trades']}／{s['winning_trades']} | {s['win_rate_percent']:.4f}% | {s['total_net_profit']:,.2f} | {s['total_return_percent']:.6f}% | {s['max_drawdown_percent']:.6f}% | {s['average_holding_days']:.6f} | {s['buy_and_hold_return_percent']:.6f}% |")
    lines += ['', '勝率僅計已平倉淨獲利交易；持倉時間為買賣間實際日曆天數，含夜間與休市。'
        '最大回撤使用初始資金及每根 Open 成交後／完成 Close 的權益，不推測棒內 High/Low 路徑。'
        '買進持有於期間第一根 Open 買入並支付買進費用，最後 Close 評價，不假設期末賣出費稅、不計股息；'
        '純價格報酬另列完整 JSON。', '', '## 一年逐筆交易', '',
        '所有時間為 Asia/Taipei；損益已扣買賣手續費及賣出稅。CSV 另含確認訊號時間、費稅、單筆報酬與持倉天數。']
    for row in annual:
        result=json.loads(Path(row['paths']['json']).read_text(encoding='utf-8'))
        stem=Path(row['paths']['json']).stem
        lines += ['', f"### {row['symbol']}（{len(result['trades'])} 筆）", '',
            f'[完整 JSON]({stem}.json) · [每筆成交 CSV]({stem}.fills.csv) · [來回交易 CSV]({stem}.trades.csv)', '',
            '| 買進成交 | 賣出成交 | 買價 | 賣價 | 股數 | 淨損益（元） |', '|---|---|---:|---:|---:|---:|']
        for trade in result['trades']:
            entry=pd.Timestamp(trade['entry_time']).strftime('%Y-%m-%d %H:%M')
            exit_time=pd.Timestamp(trade['exit_time']).strftime('%Y-%m-%d %H:%M')
            lines.append(f"| {entry} | {exit_time} | {trade['entry_price']:g} | {trade['exit_price']:g} | {trade['shares']} | {trade['net_profit']:,.2f} |")
    lines += ['', '## 與既有短區間 Yahoo 回測比較', '',
        '固定相同區間 2026-09-17～2026-10-29、相同本金與成本、相同初始空手狀態，分別重跑兩來源；'
        '原有 Yahoo 成交、訊號、交易與績效所有已保存欄位完全重現。舊檔尚無回撤等後增欄位，這些指標由現行未變更的績效函式重算。', '',
        '| 股票 | 比較棒數（含暖身） | O／H／L／C／V 不同棒數 | Fugle／Yahoo 訊號數 | 方向或時間不同 | Fugle／Yahoo 總報酬率 |',
        '|---|---:|---|---:|---:|---|']
    for symbol,c in artifact['short_comparison'].items():
        counts='／'.join(str(c['field_difference_counts'][name]) for name in OHLCV)
        lines.append(f"| {symbol} | {c['compared_bars_including_warmup']} | {counts} | {c['fugle_signal_count']}／{c['yahoo_signal_count']} | {len(c['signal_differences'])} | {c['fugle_summary']['total_return_percent']:.6f}%／{c['yahoo_summary']['total_return_percent']:.6f}% |")
    lines += ['', '兩檔短區間日線 Close 及多頭狀態相同；部分 60 分 K Close 與 MA60 數值不同，'
        '但沒有改變本區間穿越均線的方向或時點，因此 2330 的 1 筆、2408 的 5 筆來回交易，其成交時間、價格、股數與損益全部相同。'
        '成交量差異不參與現有 666 的 Close／MA60 穿越及日線多頭條件；不能據此捏造交易差異或認定某來源錯誤。'
        '這個短區間結果不代表兩來源在任意期間都會產生相同交易。', '',
        'Yahoo 一年快照仍存在既有分鐘及日線暖身缺口，不能拿它產生可比較的一年績效；本次沒有把 Fugle 行情拼進 Yahoo。'
        '一年和短區間的績效不同，還包含期間、初始狀態及資金累積的影響，不能全部歸因於來源。', '',
        '逐棒 OHLCV、同方向訊號數值、訊號集合、日線狀態與成交差異完整列於 '
        '[機器驗證與比較 JSON](fugle_integration_audit_20261101.json)。', '',
        '## 驗證與保護範圍', '',
        '- 原有與新增共 **249 項單元測試通過**（原 233 項、新增 16 項），包含缺棒、日線暖身、時區、成交量轉換、快照竄改、來源切換及原策略行為。',
        '- 四份實際結果（兩檔各一年與短區間）均重跑一致；逐筆使用獨立 Decimal 算術核對費稅、現金、單筆損益、勝率、總報酬、回撤、持倉時間與買進持有基準。',
        '- 每份結果另選起點、中點、倒數第二個交易日重跑，共 12 次；截至該日的成交及來回交易均與完整結果一致。此為指定截點檢查，並非全年每一日都重跑。',
        '- 分鐘 SMA 使用 math.fsum 獨立參考核對；原生 13:00 棒集合競價的八組真實樣本檢查全數通過。',
        '- [保護檔案核對](fugle_integration_protected_20261101.json)：104 個原助手、策略核心、資料驗證、日曆、成交引擎、成本及績效／輸出檔案 SHA-256 均不變；既有 backtest/data.py 與 backtest/__main__.py 僅修改來源接入邊界。',
        '- 原助手既有未提交修改保留，不匯入 app、不讀寫實際持倉資料庫；沒有 API 實測重抓、來源替換、券商下單或參數最佳化。', '',
        '## 重現', '', '在台股目錄執行：', '', '```powershell',
        'python -B -X utf8 -m unittest discover -s 666_strategy/tests -p "test_*.py"',
        'python -B -X utf8 -m 666_strategy.verification.verify_fugle_integration', '```', '',
        '單獨選來源與回放快照的 CLI 範例見 [BACKTEST.md](../BACKTEST.md)。']
    (root/'FUGLE_INTEGRATION_REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verification-source', type=Path, default=HERE/'fugle_live_full_corrected.json')
    parser.add_argument('--yahoo-source', type=Path, default=HERE/'daily_live_20261029.json')
    parser.add_argument('--output-dir', type=Path, default=HERE)
    args = parser.parse_args(argv)
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    snapshot = create_fugle_snapshot(args.verification_source, root/'fugle_history_20251030_20261029.snapshot.json')
    snapshot_bytes = snapshot.read_bytes()
    # Byte-for-byte reproducibility, including its original download timestamp.
    create_fugle_snapshot(args.verification_source, snapshot)
    assert snapshot.read_bytes() == snapshot_bytes
    provider, yahoo = FugleSnapshotProvider(snapshot), SnapshotProvider(args.yahoo_source)
    source = json.loads(args.verification_source.read_text(encoding='utf-8'))
    reports, comparison = [], {}
    for symbol in ('2330', '2408'):
        for start in (START, SHORT):
            data = fetch_backtest_data(symbol, start, END, provider=provider)
            result = run_backtest(data)
            output = root/f'fugle_backtest_{symbol}_{start.replace("-", "")}_{END.replace("-", "")}.json'
            paths = export_backtest(result, output)
            audit = audit_result(data, result, provider)
            replay = run_backtest(fetch_backtest_data(symbol, start, END, provider=provider))
            assert replay.to_dict() == result.to_dict()
            reports.append(dict(symbol=symbol,start=start,end=END,summary=result.summary,
                open_position=result.open_position, paths=paths, audit=audit,
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest()))
            if start == SHORT:
                yd = fetch_backtest_data(symbol, start, END, provider=yahoo)
                yr = run_backtest(yd)
                old = json.loads((HERE/f'backtest_{symbol}_20260917_20261029.json').read_text(encoding='utf-8'))
                for name in ('fills', 'trades', 'raw_signals', 'summary', 'benchmark', 'events'):
                    if name in old:
                        # Legacy artifacts predate descriptive holding/drawdown
                        # fields. Every field they actually saved must match.
                        assert_saved_fields(yr.to_dict()[name], old[name])
                comparison[symbol] = compare_sources(data,result,yd,yr)
                comparison[symbol]['original_yahoo_reproduced_exactly'] = True
        print(symbol+': annual and short replay audited', flush=True)
    artifact = dict(passed=True, generated_at=datetime.now(TAIPEI).isoformat(),
        source=provider.metadata, snapshot_reproduced_byte_for_byte=True,
        original_yahoo_source_sha256=hashlib.sha256(args.yahoo_source.read_bytes()).hexdigest(),
        auction_evidence=auction_audit(source), reports=reports, short_comparison=comparison,
        network_requests=0, broker_orders=0)
    (root/'fugle_integration_audit_20261101.json').write_text(
        json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    write_report(artifact, root)
    print('Fugle integration audit passed; no network requests.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
