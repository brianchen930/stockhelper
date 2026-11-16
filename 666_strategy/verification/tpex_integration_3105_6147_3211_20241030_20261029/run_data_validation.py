"""User-designated TPEx source integration, without strategy/AB execution."""
import argparse
from collections import defaultdict
from datetime import date
import gzip
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import ssl
import sys
from urllib.request import Request, HTTPSHandler, build_opener
from urllib.parse import urlencode

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0,str(PROJECT))
client_module = importlib.import_module('666_strategy.fugle_client')
f = importlib.import_module('666_strategy.fugle_provider')
cal_module = importlib.import_module('666_strategy.calendar')
data_module = importlib.import_module('666_strategy.backtest.data')
SYMBOLS = {'3105':'穩懋','6147':'頎邦','3211':'順達'}
START, END = '2024-10-30','2026-10-29'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,doc):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    raw = json.dumps(doc,ensure_ascii=False,indent=None if str(path).endswith('.gz') else 2,allow_nan=False).encode('utf-8')
    if str(path).endswith('.gz'):
        # Fixed gzip mtime makes the same serialized capture byte reproducible.
        raw = gzip.compress(raw,mtime=0)
    Path(path).write_bytes(raw)


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def sample():
    return dict(origin='user_designated',symbols=[s+'.TWO' for s in SYMBOLS],names=SYMBOLS,
        period=[START,END],previous_screening_conclusions_applied=False,
        selection_basis='Explicit user instruction; not selected by historical strategy performance, capitalization or volatility screening',
        no_strategy_backtest=True)


def public_get(url,name):
    path = ROOT/'public'/name
    if not path.exists():
        context = ssl.create_default_context(cafile=str(PROJECT/'666_strategy/.cache/windows_current_roots.pem'))
        opener = build_opener(HTTPSHandler(context=context))
        with opener.open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30) as response:
            raw = response.read(30*1024*1024)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
            write(path.with_name(path.name+'.meta.json'),dict(url=url,http_status=response.status,
                effective_url=response.geturl(),received_at=pd.Timestamp.now(tz='Asia/Taipei').isoformat(),sha256=sha(path)))
    return path.read_bytes()


def public():
    for year in (2024,2025,2026):
        url = 'https://www.tpex.org.tw/web/stock/aftertrading/spendi/sprc_result.php?'+urlencode(dict(y=str(year-1911),l='zh-tw',o='json'))
        doc = json.loads(public_get(url,f'official_halts_{year}.json'))
        assert str(doc['date'])==str(year), 'Server ignored historical year'
        selected = [r for t in doc.get('tables',[]) for r in t.get('data',[]) if str(r[2]) in SYMBOLS]
        calendar = json.loads(public_get('https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?date='+str(year),f'calendar_year_{year}.json'))
        assert f'{year-1911}年' in calendar['data']['html']
        print('OFFICIAL_YEAR',year,'selected halt/resume records',selected,flush=True)
    modes = json.loads(public_get('https://www.tpex.org.tw/openapi/v1/tpex_cmode','current_trading_modes.json'))
    print('CURRENT_MODES',[r for r in modes if r.get('SecuritiesCompanyCode') in SYMBOLS],flush=True)


class RecordingClient:
    def __init__(self):
        self.client = None
        self.requests = []

    def candles(self,spec):
        suffix = '.json.gz' if spec['timeframe']=='1' else '.json'
        name = f"{spec['symbol']}_{spec['timeframe']}_{spec['start']}_{spec['end']}"+suffix
        path = ROOT/'downloads'/name
        if path.exists():
            item = read(path)
            assert item['request']==spec and item['http_status']==200
            rows = item['rows']
            # Revalidate using original source fields, exchange and volume units.
            payload = dict(symbol=spec['symbol'],exchange='TPEx',market='OTC',timeframe=spec['timeframe'],adjusted=False,
                data=[dict(date=r['source_timestamp'],open=r['Open'],high=r['High'],low=r['Low'],close=r['Close'],volume=r['source_volume']) for r in rows])
            assert client_module.normalize(payload,spec)==rows
            origin = 'verified_saved_capture'
        else:
            if self.client is None:
                self.client = client_module.FugleClient(ca_bundle=str(PROJECT/'666_strategy/.cache/windows_current_roots.pem'))
            try:
                rows = self.client.candles(spec)
            except client_module.ProbeError as e:
                write(ROOT/'failures'/name.replace('.gz',''),dict(request=spec,error=str(e),
                    http_status=e.http_status,api_error=e.api_error,no_fallback=True))
                raise
            item = dict(request=spec,http_status=self.client.last_http_status,exchange='TPEx',market='OTC',
                received_at=pd.Timestamp.now(tz='Asia/Taipei').isoformat(),rows=rows)
            write(path,item)
            origin = 'new_fugle_request'
        self.requests.append(dict(file=name,sha256=sha(path),request=spec,rows=len(rows),received_at=item['received_at'],origin=origin))
        print(spec['symbol'],spec['timeframe'],spec['start']+'..'+spec['end'],len(rows),origin,flush=True)
        return rows


def clock_evidence(code,recorder,minute_rows):
    native = {r['bar_start']:r for r in minute_rows}
    groups = defaultdict(list)
    for first,last in client_module.chunks(date(2024,10,14),date(2026,10,29)):
        rows = recorder.candles(dict(symbol=code,timeframe='1',start=str(first),end=str(last),exchange='TPEx',market='OTC'))
        for row in rows:
            at = pd.Timestamp(row['bar_start'])
            groups[(str(at.date()), min(at.hour,13))].append(row)
    comparisons,late,unexpected = [],[],[]
    for (day,hour),rows in sorted(groups.items()):
        rows.sort(key=lambda r:r['bar_start'])
        stamp = f'{day}T{hour:02d}:00:00+08:00'
        aggregate = dict(Open=rows[0]['Open'],High=max(r['High'] for r in rows),Low=min(r['Low'] for r in rows),
            Close=rows[-1]['Close'],Volume=math.fsum(r['Volume'] for r in rows))
        base = native.get(stamp)
        passed = base is not None and all(math.isclose(base[k],aggregate[k],rel_tol=0,abs_tol=1e-6)
            for k in ('Open','High','Low','Close','Volume'))
        comparisons.append(dict(bar_start=stamp,first_minute=rows[0]['bar_start'],last_minute=rows[-1]['bar_start'],
            native=base,minute_aggregate=aggregate,passed=passed))
        if hour==13 and pd.Timestamp(rows[-1]['bar_start']).time()>pd.Timestamp('13:30').time():
            late.append(dict(date=day,last_minute=rows[-1]['bar_start'],close=aggregate['Close']))
        if hour not in (9,10,11,12,13):
            unexpected.append(stamp)
    missing = sorted(set(native)-{r['bar_start'] for r in comparisons})
    evidence = dict(status='ALL_OBSERVED_BARS_AGREE' if not missing and all(r['passed'] for r in comparisons) else 'SOURCE_TIME_OR_AGGREGATION_MISMATCH',
        symbol=code+'.TWO',checked_native_bars=len(comparisons),missing_minute_evidence=missing,
        mismatches=[r for r in comparisons if not r['passed']],late_closes=late,unexpected_hours=unexpected,
        completion_at_1330_verified=not late and not missing and all(r['passed'] for r in comparisons),
        minute_source_used_for_validation_only=True,native_60m_prices_changed=False)
    write(ROOT/f'{code}_closing_time_validation.json',evidence)
    write(ROOT/f'{code}_minute_to_native_comparisons.json.gz',comparisons)
    print(code,'CLOSING_TIME',json.dumps({k:v for k,v in evidence.items() if k!='mismatches'},ensure_ascii=False),flush=True)
    return evidence


def capture():
    write(ROOT/'research_sample.json',sample())
    cal,_ = cal_module.bundled_calendar(date(2024,5,6),date(2026,10,29))
    window = data_module.data_window(date.fromisoformat(START),date.fromisoformat(END),calendar=cal)
    recorder = RecordingClient()
    provider = f.FugleProvider(client=recorder)
    outcomes = []
    for code,name in SYMBOLS.items():
        pair = code+'.TWO'
        first_req = len(recorder.requests)
        minute = provider.history(pair,start=window.minute_start,end=pd.Timestamp('2026-10-30',tz='Asia/Taipei'),interval='60m')
        daily = provider.history(pair,start=window.daily_start,end=pd.Timestamp('2026-10-30',tz='Asia/Taipei'),interval='1d')
        source_requests = recorder.requests[first_req:]
        captured_minute = next(s['rows'] for s in provider.snapshots if s['symbol']==pair and s['interval']=='60m')
        clock = clock_evidence(code,recorder,captured_minute)
        expected_minute = {pd.Timestamp(f'{day} {hour:02d}:00',tz='Asia/Taipei')
            for day in cal.sessions if day>=window.minute_start.date() for hour in (9,10,11,12,13)}
        expected_daily = {pd.Timestamp(str(day),tz='Asia/Taipei') for day in cal.sessions if day>=window.daily_start.date()}
        missing_minute = sorted(expected_minute-set(minute.index))
        missing_daily = sorted(expected_daily-set(daily.index))
        unexpected_minute = sorted(set(minute.index)-expected_minute)
        unexpected_daily = sorted(set(daily.index)-expected_daily)
        strict_error = None
        try:
            prepared = data_module.prepare_data(minute,daily,symbol=pair,start=START,end=END,calendar=cal)
            assert len(prepared.history.bars)==2480 and len(prepared.daily.bars)==604
        except data_module.BacktestDataError as e:
            strict_error = dict(code=e.code,details=e.details)
        parts = []
        for request in source_requests:
            saved = read(ROOT/'downloads'/request['file'])
            parts.append(dict(request=saved['request'],status='FETCHED',http_status=200,rows=saved['rows'],origin=request))
        received_at = max(request['received_at'] for request in recorder.requests)
        source = dict(schema='fugle_feasibility_v2',status='NATIVE_COVERAGE_PASSED_REVIEW_COMPARISONS' if strict_error is None else 'INCOMPLETE_NATIVE_COVERAGE',
            generated_at=received_at,requested_start=START,requested_end=END,
            daily_warmup_start=window.daily_start.isoformat(),minute_warmup_start=window.minute_start.isoformat(),
            normalized_volume_unit='shares',full_range_results=parts,research_sample=sample(),closing_time_validation=clock)
        source_path = ROOT/f'{code}_source.json'
        write(source_path,source)
        snapshot_path = ROOT/f'{code}_20241030_20261029.snapshot.json'
        if strict_error is None:
            f.create_fugle_snapshot(source_path,snapshot_path)
            before = snapshot_path.read_bytes()
            f.create_fugle_snapshot(source_path,snapshot_path)
            assert before==snapshot_path.read_bytes()
            offline = f.FugleSnapshotProvider(snapshot_path)
            for interval,expected in [('60m',minute),('1d',daily)]:
                result = offline.history(pair,start=window.minute_start if interval=='60m' else window.daily_start,
                    end=pd.Timestamp('2026-10-30',tz='Asia/Taipei'),interval=interval)
                pd.testing.assert_frame_equal(result,expected)
            # Existing data gate only, no run_backtest/evaluate_decision call.
            replay = data_module.fetch_backtest_data(pair,START,END,provider=offline)
            assert replay.symbol==pair and replay.metadata['exchange']=='TPEx' and replay.metadata['market']=='OTC'
            snapshot_result = dict(path=snapshot_path.name,sha256=sha(snapshot_path),deterministic=True,offline_exact=True,
                existing_data_gate_passed=True)
        else:
            snapshot_result = dict(created=False,reason='Existing strict gate failed; source captures retained without fabricating prices')
        outcome = dict(symbol=pair,name=name,research_sample_origin='user_designated',
            minute_bars=len(minute),daily_bars=len(daily),expected_minute=2480,expected_daily=604,run_sessions=len(window.sessions),
            minute_warmup_bars=60,daily_warmup_sessions=120,minute_start=window.minute_start.isoformat(),daily_start=window.daily_start.isoformat(),
            missing_minute=[s.isoformat() for s in missing_minute],missing_daily=[s.isoformat() for s in missing_daily],
            unexpected_minute=[s.isoformat() for s in unexpected_minute],unexpected_daily=[s.isoformat() for s in unexpected_daily],
            strict_error=strict_error,clock=clock,snapshot=snapshot_result)
        outcomes.append(outcome)
        write(ROOT/'data_validation.json',dict(period=[START,END],research_sample=sample(),symbols=outcomes,
            no_strategy_backtest=True,calendar_basis='Unchanged bundled 2024-2026 calendar; official TPEx reconciliation recorded separately'))
        print(code,'DATA_VALIDATION',json.dumps({k:v for k,v in outcome.items() if k!='clock'},ensure_ascii=False),flush=True)
    write(ROOT/'request_manifest.json',recorder.requests)
    print('Native TPEx source capture and snapshot replay completed; no AB backtest.',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('public','capture'))
    args = parser.parse_args()
    try:
        globals()[args.stage]()
    except Exception as e:
        write(ROOT/f'{args.stage}_failure.json',dict(error_type=type(e).__name__,detail=str(e),
            period=[START,END],no_fabrication=True,no_shortening=True,no_strategy_backtest=True))
        raise


if __name__=='__main__':main()
