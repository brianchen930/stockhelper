"""Read-only TPEx feasibility research. Never imports or runs strategy runners."""
import argparse
from datetime import date, datetime, timedelta
import hashlib
import importlib
import io
import json
import math
from pathlib import Path
import re
import ssl
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0,str(PROJECT))
fc = importlib.import_module('666_strategy.fugle_client')
calendar_module = importlib.import_module('666_strategy.calendar')
TAIPEI = 'Asia/Taipei'
POOL = {'1565':('精華','生技醫療／隱形眼鏡','LOW_VOL'),
        '4105':('東洋','生技醫療／藥品','LOW_VOL'),
        '3260':('威剛','半導體／記憶體模組','LIQUID'),
        '6147':('頎邦','半導體／封裝測試','LIQUID'),
        '3552':('同致','汽車工業／車用電子','HIGH_VOL'),
        '6125':('廣運','電機機械／自動化設備','HIGH_VOL'),
        '6188':('廣明','電腦及週邊／儲存與機器人','HIGH_VOL')}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def freeze():
    policy = dict(created_at=datetime.now().astimezone().isoformat(),cutoff='2024-10-29',
        pool={k:dict(name=v[0],industry=v[1],group=v[2]) for k,v in POOL.items()},
        feature_window='Last 60 sessions ending 2024-10-29; previous close for 60 log returns',
        market_cap_date='2024-09-30 (last complete calendar month before cutoff)',
        common_gates=dict(market_cap_TWD=[5e9,50e9],median_daily_turnover_TWD_min=1e7,
            median_daily_volume_shares_min=100000,required_preperiod_sessions=60),
        selection=dict(LOW_VOL='Lowest sample standard deviation of 60 daily log returns',
            LIQUID='Highest median daily turnover',HIGH_VOL='Highest sample standard deviation of 60 daily log returns'),
        tie_break='Lower numeric symbol; no replacements based on future coverage or backtest performance',
        limitations='Purposefully fixed seven-stock candidate pool, not exhaustive or random universe; no performance ranking',
        no_strategy_backtest=True,no_parameter_optimization=True)
    path = ROOT/'selection_policy.json'
    if not path.exists():
        write(path,policy)
    protected = ROOT/'protected_baseline.json'
    if not protected.exists():
        paths = list((PROJECT/'666_strategy').rglob('*.py'))+list((PROJECT/'666_strategy/calendars').glob('*.json'))
        paths += list((PROJECT/'app').rglob('*.py'))+list(PROJECT.glob('*.py'))
        write(protected,{p.relative_to(PROJECT).as_posix():sha(p) for p in paths if ROOT not in p.parents})


def context():
    return ssl.create_default_context(cafile=str(PROJECT/'666_strategy/.cache/windows_current_roots.pem'))


def public_get(url, name):
    path = ROOT/'public'/name
    if path.exists():
        return path.read_bytes()
    opener = build_opener(HTTPSHandler(context=context()))
    req = Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/html'})
    try:
        with opener.open(req,timeout=25) as response:
            raw = response.read(20*1024*1024)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
            write(path.with_suffix(path.suffix+'.meta.json'),dict(url=url,status=response.status,
                received_at=pd.Timestamp.now(tz=TAIPEI).isoformat(),sha256=sha(path)))
            return raw
    except Exception as e:
        print('PUBLIC_REQUEST_FAILED',name,type(e).__name__,flush=True)
        return None


def public():
    raw = public_get('https://www.tpex.org.tw/openapi/swagger.json','swagger.json')
    if raw:
        doc = json.loads(raw)
        for path,item in doc['paths'].items():
            if any(s in path for s in ('market_value','03_O','close_quotes')):
                print(path, list(item.values())[0].get('summary',''),flush=True)
    raw = public_get('https://www.tpex.org.tw/zh-tw/mainboard/trading/statistics/month/value.html','monthly_value.html')
    if raw:
        text = raw.decode('utf-8')
        print('SCRIPTS',re.findall(r'<script[^>]+src=[\"\x27]([^\"\x27]+)',text),flush=True)
        print('API_HINTS',re.findall(r'.{0,100}(?:api|report|market|value|Cap).{0,100}',text)[-35:],flush=True)
    js = public_get('https://www.tpex.org.tw/rsrc/js/tables.js','tables.js')
    if js:
        t = js.decode('utf-8')
        print('JS_HINTS',re.findall(r'.{0,160}(?:/www/|\.json|/api/|ajax|action|path).{0,220}',t)[:25])
    url = 'https://www.tpex.org.tw/www/zh-tw/statistics/monthlyRptMkt?'+urlencode(dict(type='9',startDate='2024/09/01',endDate='2024/09/30',response='json'))
    raw = public_get(url,'market_cap_month_202409.json')
    if raw:
        print('MONTHLY_CAP',raw.decode('utf-8')[:5000])
    url = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'
    raw = public_get(url,'current_company_profiles.json')
    if raw:
        doc = json.loads(raw)
        print('PROFILES',str(doc[:1])[:1700])
    raw = public_get('https://www.tpex.org.tw/www/zh-tw/statistics/monthlyRptMktDl?doc=2708&isOds=Y','market_cap_202409.ods')
    if raw:
        doc = ET.fromstring(zipfile.ZipFile(io.BytesIO(raw)).read('content.xml'))
        ns = {'table':'urn:oasis:names:tc:opendocument:xmlns:table:1.0','text':'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
        records = []
        for tab in doc.findall('.//table:table',ns):
            for row in tab.findall('table:table-row',ns):
                cells = []
                for cell in row.findall('table:table-cell',ns):
                    text = '\n'.join(''.join(p.itertext()) for p in cell.findall('text:p',ns))
                    n = int(cell.get('{'+ns['table']+'}number-columns-repeated','1'))
                    cells.extend([text]*min(n,30))
                records.append(cells)
        write(ROOT/'public/market_cap_202409_rows.json',records)
        print('CAP_HEADERS',records[:6])
        print('CAP_POOL',[r for r in records if any(c in POOL for c in r)])
    for name,url in [
        ('cap_old_20240930.json','https://www.tpex.org.tw/web/stock/aftertrading/daily_mktval/mkt_result.php?l=zh-tw&d=113/09/30&o=json'),
        ('quotes_20240930.json','https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2024/09/30&type=EW&response=json'),
        ('current_market_cap.json','https://www.tpex.org.tw/openapi/v1/tpex_daily_market_value')]:
        raw = public_get(url,name)
        if raw:
            print('EXTRA',name,raw.decode('utf-8')[:1800])


class RawAPI:
    """Separate probe transport, preserving actual TPEx metadata; not a Provider."""
    def __init__(self):
        self.key = fc.read_api_key()
        assert self.key,'MISSING_API_KEY'
        self.opener = build_opener(fc.NoRedirect(),HTTPSHandler(context=context()))
        self.last = 0

    def candles(self,symbol,tf,start,end,purpose):
        assert symbol in POOL and tf in ('60','D','1')
        path = ROOT/'captures'/f'{symbol}_{tf}_{start}_{end}.json'
        spec = dict(symbol=symbol,timeframe=tf,start=start,end=end)
        if path.exists():
            saved = read(path)
            assert saved['request']==spec and saved['http_status']==200
        else:
            fields = 'open,high,low,close,volume'+(',turnover' if tf=='D' else '')
            params = {'from':str(start),'to':str(end),'timeframe':tf,'fields':fields,'sort':'asc'}
            if tf=='D':
                params['adjusted']='false'
            req = Request(fc.BASE+symbol+'?'+urlencode(params),headers={'X-API-KEY':self.key,'Accept':'application/json'})
            time.sleep(max(0,1.1-(time.monotonic()-self.last)))
            self.last = time.monotonic()
            try:
                with self.opener.open(req,timeout=25) as response:
                    payload = json.load(response)
                    assert response.status==200
            except HTTPError as e:
                message = fc.safe_api_error(e.read(fc.ERROR_BODY_LIMIT+1),self.key)
                write(ROOT/'failures'/path.name,dict(request=spec,http_status=e.code,api_error=message))
                raise RuntimeError('FUGLE_HTTP_'+str(e.code)) from None
            except Exception:
                raise RuntimeError('FUGLE_NETWORK_TLS_OR_JSON_ERROR') from None
            # Data-only allowlist; never save authentication or request headers.
            safe = {k:payload[k] for k in ('symbol','exchange','market','type','timeframe','adjusted','sort') if k in payload}
            safe['data'] = [{k:r[k] for k in ('date','open','high','low','close','volume','turnover') if k in r} for r in payload.get('data',[])]
            saved = dict(request=spec,query=params,http_status=200,purpose=purpose,
                received_at=pd.Timestamp.now(tz=TAIPEI).isoformat(),payload=safe)
            try:
                validate(safe,spec)
            except AssertionError as e:
                write(ROOT/'failures'/path.name,dict(**saved,status='SOURCE_VALIDATION_REJECTED',error=str(e)))
                raise
            write(path,saved)
        rows = validate(saved['payload'],spec)
        print(symbol,tf,str(start)+'..'+str(end),len(rows),'HTTP200',saved['payload']['exchange'],flush=True)
        return rows


def validate(payload,spec):
    assert payload['symbol']==spec['symbol'] and str(payload['timeframe'])==spec['timeframe']
    assert payload['exchange']=='TPEx' and payload.get('market','OTC')=='OTC'
    assert payload.get('adjusted',False) is False
    rows,seen = [],set()
    for raw in payload['data']:
        stamp = pd.Timestamp(raw['date'])
        if spec['timeframe']=='D':
            assert re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw['date'])
            stamp = stamp.tz_localize(TAIPEI)
        else:
            assert stamp.tzinfo is not None
            stamp = stamp.tz_convert(TAIPEI)
        assert str(spec['start'])<=str(stamp.date())<=str(spec['end'])
        assert stamp not in seen
        seen.add(stamp)
        values = [float(raw[k]) for k in ('open','high','low','close','volume')]
        assert all(math.isfinite(v) for v in values)
        o,h,l,c,v = values
        assert min(o,h,l,c)>0 and l<=min(o,c)<=max(o,c)<=h and v>=0
        row = dict(bar_start=stamp.isoformat(),Open=o,High=h,Low=l,Close=c,Volume=v*(1 if spec['timeframe']=='D' else 1000),source_volume=v)
        if 'turnover' in raw:
            row['turnover']=float(raw['turnover'])
        rows.append(row)
    assert rows,'EMPTY_DATA'
    return sorted(rows,key=lambda r:r['bar_start'])


def screen():
    policy = read(ROOT/'selection_policy.json')
    quotes = read(ROOT/'public/quotes_20240930.json')
    assert quotes['tables'][0]['date']=='113/09/30'
    caps = {r[0]:float(r[2].replace(',',''))*int(r[14].replace(',',''))
            for t in quotes['tables'] for r in t.get('data',[]) if r[0] in POOL}
    profiles = {r['SecuritiesCompanyCode']:r for r in read(ROOT/'public/current_company_profiles.json')}
    cal,_ = calendar_module.bundled_calendar(date(2024,5,6),date(2024,10,29))
    days = [s for s in cal.sessions if s<=date(2024,10,29)][-61:]
    api = RawAPI()
    metrics = []
    for code,(name,industry,group) in POOL.items():
        rows = api.candles(code,'D',str(days[0]),str(days[-1]),'PREPERIOD_FEATURES_ONLY')
        frame = pd.DataFrame(rows).set_index('bar_start')
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index,utc=True)).tz_convert(TAIPEI)
        assert list(frame.index.date)==days
        assert profiles[code]['DateOfListing']<'20240506'
        close = frame.Close
        returns = pd.Series([math.log(close.iloc[i]/close.iloc[i-1]) for i in range(1,len(close))])
        vol = float(returns.std(ddof=1)*100)
        sample = frame.iloc[-60:]
        # Actual Fugle daily turnover is requested, not Close*Volume as a proxy.
        assert 'turnover' in sample.columns and sample.turnover.gt(0).all()
        turnover = float(sample.turnover.median())
        volume = float(sample.Volume.median())
        eligible = 5e9<=caps[code]<=50e9 and turnover>=1e7 and volume>=100000
        metrics.append(dict(symbol=code,name=name,industry=industry,group=group,
            preperiod_start=str(days[1]),preperiod_end=str(days[-1]),prior_close_date=str(days[0]),
            daily_log_return_sigma_percent=vol,annualized_sigma_percent=vol*math.sqrt(252),
            median_turnover_TWD=turnover,median_volume_shares=volume,market_cap_TWD=caps[code],
            market_cap_date='2024-09-30',current_official_industry_code=profiles[code]['SecuritiesIndustryCode'],
            listing_date=profiles[code]['DateOfListing'],eligible=eligible))
    selected = []
    for group in ('LOW_VOL','LIQUID','HIGH_VOL'):
        candidates = [r for r in metrics if r['group']==group and r['eligible']]
        if group=='LOW_VOL':
            candidates.sort(key=lambda r:(r['daily_log_return_sigma_percent'],int(r['symbol'])))
        elif group=='LIQUID':
            candidates.sort(key=lambda r:(-r['median_turnover_TWD'],int(r['symbol'])))
        else:
            candidates.sort(key=lambda r:(-r['daily_log_return_sigma_percent'],int(r['symbol'])))
        assert candidates,'No eligible candidate; do not relax the frozen gates: '+group
        selected.append(candidates[0])
    path = ROOT/'selection_result.json'
    output = dict(policy_sha256=sha(ROOT/'selection_policy.json'),features=metrics,selected=selected,
        information_cutoff='2024-10-29',no_strategy_backtest=True,
        note='Only preperiod daily data read before selection was sealed; current profile used only for identity/industry, not capitalization.')
    if path.exists():
        assert read(path)==output
    else:
        write(path,output)
    print('FEATURES',json.dumps(metrics,ensure_ascii=False),flush=True)
    print('SEALED_SELECTION',[(r['symbol'],r['name'],r['group']) for r in selected],sha(path),flush=True)


def coverage():
    selection = read(ROOT/'selection_result.json')
    assert selection['policy_sha256']==sha(ROOT/'selection_policy.json')
    # Freeze candidates before requesting ANY in-period source prices.
    selected_digest = sha(ROOT/'selection_result.json')
    cal,_ = calendar_module.bundled_calendar(date(2024,5,6),date(2026,10,29))
    sessions = [s for s in cal.sessions if s>=date(2024,10,30)]
    previous = [s for s in cal.sessions if s<date(2024,10,30)]
    assert len(sessions)==484 and len(previous)==120
    minute_sessions = previous[-12:]+sessions
    daily_sessions = previous+sessions
    expected_minute = {pd.Timestamp(str(day)+' '+str(hour)+':00',tz=TAIPEI).isoformat()
                       for day in minute_sessions for hour in (9,10,11,12,13)}
    expected_daily = {pd.Timestamp(str(day),tz=TAIPEI).isoformat() for day in daily_sessions}
    api = RawAPI()
    outcomes = []
    provider_module = importlib.import_module('666_strategy.fugle_provider')
    for candidate in selection['selected']:
        code = candidate['symbol']
        series = {}
        checks = {}
        for tf,start,expected in [('60',date(2024,10,14),expected_minute),('D',date(2024,5,6),expected_daily)]:
            records = []
            for first,last in fc.chunks(start,date(2026,10,29)):
                records += api.candles(code,tf,str(first),str(last),'FULL_COVERAGE_ONLY_NO_STRATEGY')
            stamps = [r['bar_start'] for r in records]
            observed = set(stamps)
            missing = sorted(expected-observed)
            unexpected = sorted(observed-expected)
            duplicates = sorted(s for s in observed if stamps.count(s)>1)
            checks[tf] = dict(expected=len(expected),observed=len(records),missing=missing,
                unexpected=unexpected,duplicates=duplicates,zero_volume=sum(r['Volume']==0 for r in records),
                passed=not missing and not unexpected and not duplicates)
            series[tf] = sorted(records,key=lambda r:r['bar_start'])
        # Actual unchanged Provider and client behavior, with no live request by Provider.
        try:
            provider_module.twse_symbol(code+'.TWO')
            provider_status = 'UNEXPECTED_ACCEPTANCE'
        except fc.ProbeError as e:
            provider_status = str(e)
        first_path = ROOT/f'captures/{code}_60_2024-10-14_2025-04-11.json'
        real = read(first_path)
        try:
            fc.normalize(real['payload'],real['request'])
            client_status = 'UNEXPECTED_ACCEPTANCE'
        except fc.ProbeError as e:
            client_status = str(e)
        assert provider_status=='FUGLE_UNSUPPORTED_SYMBOL' and client_status=='METADATA_MISMATCH'
        auctions = []
        for day in ('2024-10-30','2026-10-29'):
            records = api.candles(code,'1',day,day,'CLOSING_AUCTION_SAMPLE_ONLY')
            tail = [r for r in records if pd.Timestamp(r['bar_start']).hour==13]
            expected_bar = next((r for r in series['60'] if r['bar_start']==day+'T13:00:00+08:00'),None)
            aggregate = dict(Open=tail[0]['Open'],High=max(r['High'] for r in tail),Low=min(r['Low'] for r in tail),
                Close=tail[-1]['Close'],Volume=sum(r['Volume'] for r in tail)) if tail else None
            passed = expected_bar is not None and aggregate is not None and tail[-1]['bar_start']==day+'T13:30:00+08:00' and all(
                aggregate[k]==expected_bar[k] for k in ('Open','High','Low','Close','Volume'))
            auctions.append(dict(date=day,passed=passed,native_13_00=expected_bar,minute_aggregate=aggregate,
                auction_13_30=tail[-1] if tail else None))
        intraday = {r['bar_start'][:10]:r['Close'] for r in series['60'] if pd.Timestamp(r['bar_start']).hour==13}
        daily = {r['bar_start'][:10]:r['Close'] for r in series['D']}
        price_mismatches = [dict(date=d,intraday_close=c,daily_close=daily.get(d)) for d,c in intraday.items() if c!=daily.get(d)]
        # Fixed-capital capacity diagnostic over all bars, NOT orders or a strategy replay.
        run_bars = [r for r in series['60'] if r['bar_start'][:10]>='2024-10-30']
        positive = [r for r in run_bars if r['Volume']>0]
        participation = pd.Series([1e6/(r['Open']*r['Volume'])*100 for r in positive])
        capacity = dict(not_strategy=True,amount_TWD=1000000,denominator='Open * whole-session-bar volume shares; proxy, not opening depth',
            positive_volume_bars=len(positive),zero_volume_bars=len(run_bars)-len(positive),
            participation_percent_quantiles={str(q):float(participation.quantile(q)) for q in (0,.5,.9,.95,1)},
            percent_bars_over_1pct=float((participation>1).mean()*100),
            percent_bars_over_5pct=float((participation>5).mean()*100),
            percent_bars_over_10pct=float((participation>10).mean()*100))
        snapshot = dict(schema='tpex_feasibility_raw_v1',not_backtest_provider_snapshot=True,
            symbol=code,exchange='TPEx',market='OTC',period=['2024-10-30','2026-10-29'],
            policy_sha256=sha(ROOT/'selection_policy.json'),sealed_selection_sha256=selected_digest,
            normalized_volume_unit='shares',original_minute_volume_unit='lots',original_daily_volume_unit='shares',
            source_series=series,validation=checks,auctions=auctions)
        snapshot['payload_sha256'] = hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        snap_path = ROOT/f'{code}_feasibility.snapshot.json'
        write(snap_path,snapshot)
        outcome = dict(**candidate,coverage=checks,auction_samples=auctions,close_comparison_mismatches=price_mismatches,
            capacity_diagnostic=capacity,api_exchange='TPEx',api_market='OTC',existing_provider_status=provider_status,
            existing_client_status=client_status,snapshot_sha256=sha(snap_path),
            api_full_grid_passed=all(x['passed'] for x in checks.values()),
            official_TPEx_calendar_independently_certified=False)
        outcomes.append(outcome)
        write(ROOT/'coverage_result.json',dict(status='API_PROBED_PROVIDER_UNSUPPORTED',selected_digest=selected_digest,
            period=['2024-10-30','2026-10-29'],no_strategy_backtest=True,symbols=outcomes,
            calendar='Existing 2024-2026 bundled session calendar; grid results conditional on it; official TPEx calendar reconciliation pending'))
        print(code,'COVERAGE',json.dumps(checks,ensure_ascii=False),'CAPACITY',json.dumps(capacity),flush=True)
    baseline = read(ROOT/'protected_baseline.json')
    changed = [p for p,h in baseline.items() if not (PROJECT/p).exists() or sha(PROJECT/p)!=h]
    assert not changed
    assert sha(ROOT/'selection_result.json')==selected_digest
    write(ROOT/'validation.json',dict(status='PASSED_FEASIBILITY_ONLY',no_strategy_backtest=True,no_strategy_provider_app_changes=True,
        protected_existing_files=len(baseline),changed_existing_files=changed,selection_policy_sha256=sha(ROOT/'selection_policy.json'),
        selection_result_sha256=selected_digest,coverage_result_sha256=sha(ROOT/'coverage_result.json'),
        probe_code_sha256=sha(__file__),calendar_sha256={str(y):sha(PROJECT/f'666_strategy/calendars/{y}.json') for y in (2024,2025,2026)}))
    print('Feasibility probes completed; no strategy backtests executed.',flush=True)


def corroborate():
    notice = public_get('https://www.tpex.org.tw/storage/eb_data/11409/11402023641.html','3260_halt_20250930.html')
    if notice:
        text = notice.decode('utf-8')
        assert '3260' in text and '114年10月1日' in re.sub(r'\s+','',text)
    raw = public_get('https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2025/10/01&type=EW&response=json','quotes_20251001.json')
    market = json.loads(raw) if raw else None
    print('MARKET_20251001',[(t.get('date'),[r for r in t.get('data',[]) if r and r[0]=='3260']) for t in market.get('tables',[])] if market else None)
    retries = []
    api = RawAPI()
    for tf in ('60','D'):
        try:
            rows = api.candles('3260',tf,'2025-10-01','2025-10-01','EXACT_HALT_DAY_RETRY')
            retries.append(dict(timeframe=tf,status='FETCHED',rows=len(rows)))
        except (RuntimeError,AssertionError) as e:
            retries.append(dict(timeframe=tf,status=str(e)))
    write(ROOT/'halt_corroboration.json',dict(symbol='3260',date='2025-10-01',
        official_notice_url='https://www.tpex.org.tw/storage/eb_data/11409/11402023641.html',
        official_notice_saved=bool(notice),notice_sha256=sha(ROOT/'public/3260_halt_20250930.html') if notice else None,
        exact_day_retries=retries,no_filling=True,no_calendar_edits=True,
        interpretation='Confirmed security-specific halt, not a market holiday; existing strict grid cannot accept it unchanged.'))
    print('HALT_RETRIES',retries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('freeze','public','screen','coverage','corroborate'))
    args = parser.parse_args()
    freeze()
    if args.stage=='freeze':
        print('Selection criteria frozen:',sha(ROOT/'selection_policy.json'))
    elif args.stage=='public':
        public()
    else:
        globals()[args.stage]()


if __name__=='__main__':
    raise SystemExit('Retired candidate study. Reusable helpers retained; use tpex_integration_3105_6147_3211_20241030_20261029 for the user-designated samples.')
