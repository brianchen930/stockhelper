"""Synthetic HTTP payload tests; these do not prove live Fugle availability."""
from datetime import date
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pandas as pd

f = importlib.import_module('666_strategy.verification.verify_fugle_history')


def spec(tf='60', day='2026-02-03'):
    return dict(symbol='2330', timeframe=tf, start=day, end=day, purpose='TEST')


def payload(tf='60', stamps=None):
    stamps = stamps or (['2026-02-03'] if tf == 'D' else ['2026-02-03T09:00:00+08:00'])
    return dict(symbol='2330', exchange='TWSE', timeframe=tf,
                data=[dict(date=at, open=100, high=103, low=99, close=102, volume=12) for at in stamps])


class Response:
    status = 200
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.body


class Opener:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.request = body, error, None
    def open(self, request, timeout):
        self.request = request
        if self.error:
            raise self.error
        return Response(json.dumps(self.body).encode())


class FugleProbeTests(unittest.TestCase):
    def setUp(self):
        # Never read the developer's real .env or send real credentials in tests.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project_root = Path(directory.name)
        project = patch.object(f, 'PROJECT_ROOT', self.project_root)
        project.start()
        self.addCleanup(project.stop)
        sleeper = patch.object(f.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_environment_key_wins_without_reading_dotenv(self):
        with patch.object(Path, 'open', side_effect=AssertionError('must not read .env')):
            self.assertEqual(f.read_api_key({'FUGLE_API_KEY': ' environment-test '}), 'environment-test')
            with patch.dict(os.environ, {'FUGLE_API_KEY': 'environment-test'}, clear=True):
                self.assertEqual(f.read_api_key(), 'environment-test')

    def test_dotenv_supports_bom_export_quotes_comments_and_blank_lines(self):
        (self.project_root / '.env').write_text(
            '# local credentials\n\nexport FUGLE_API_KEY="dotenv-test" # comment\nOTHER_SETTING=test\n',
            encoding='utf-8-sig')
        for value in ('', '   '):
            environ = {'FUGLE_API_KEY': value, 'OTHER_SETTING': 'original'}
            self.assertEqual(f.read_api_key(environ), 'dotenv-test')
            self.assertEqual(environ, {'FUGLE_API_KEY': value, 'OTHER_SETTING': 'original'})
        with patch.dict(os.environ, {'OTHER_SETTING': 'original'}, clear=True):
            self.assertEqual(f.read_api_key(), 'dotenv-test')
            self.assertEqual(dict(os.environ), {'OTHER_SETTING': 'original'})

    def test_dotenv_single_quotes_and_no_variable_interpolation(self):
        (self.project_root / '.env').write_text("FUGLE_API_KEY='literal-${OTHER_KEY}'\n", encoding='utf-8')
        with patch.dict(os.environ, {'OTHER_KEY': 'unrelated-secret'}, clear=True):
            self.assertEqual(f.read_api_key(), 'literal-${OTHER_KEY}')

    def test_dotenv_path_is_independent_of_working_directory(self):
        (self.project_root / '.env').write_text('FUGLE_API_KEY=project-test\n', encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '.env').write_text('FUGLE_API_KEY=decoy-test\n', encoding='utf-8')
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(f.read_api_key({}), 'project-test')
            finally:
                os.chdir(previous)

    def test_dotenv_missing_empty_or_without_key_keeps_missing_key_error(self):
        self.assertEqual(f.read_api_key({}), '')
        for content in ('', 'OTHER_SETTING=test\n', 'FUGLE_API_KEY\n', 'FUGLE_API_KEY=""\n'):
            (self.project_root / '.env').write_text(content, encoding='utf-8')
            with self.assertRaisesRegex(f.ProbeError, '^MISSING_API_KEY$'):
                f.FugleProbeClient(environ={})

    def test_invalid_environment_key_does_not_fall_back_to_dotenv(self):
        (self.project_root / '.env').write_text('FUGLE_API_KEY=dotenv-test\n', encoding='utf-8')
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_API_KEY_FORMAT$'):
            f.FugleProbeClient(environ={'FUGLE_API_KEY': 'Bearer invalid'})

    def test_dotenv_key_uses_existing_format_validation(self):
        for value in ('"Bearer invalid"', '"abc\\ndef"', '"abc\\rdef"'):
            (self.project_root / '.env').write_text('FUGLE_API_KEY='+value+'\n', encoding='utf-8')
            with self.assertRaisesRegex(f.ProbeError, '^INVALID_API_KEY_FORMAT$'):
                f.FugleProbeClient(environ={})

    def test_dotenv_read_errors_hide_exception_contents(self):
        with patch.object(Path, 'open', side_effect=PermissionError('synthetic-file-secret')):
            with self.assertRaisesRegex(f.ProbeError, '^ENV_FILE_READ_ERROR$'):
                f.read_api_key({})
        (self.project_root / '.env').write_bytes(b'FUGLE_API_KEY=synthetic-file-secret\xff')
        with self.assertRaisesRegex(f.ProbeError, '^ENV_FILE_READ_ERROR$'):
            f.read_api_key({})

    def test_dotenv_key_report_presence_http_stop_and_redaction(self):
        key = 'synthetic-dotenv-secret'
        (self.project_root / '.env').write_text('FUGLE_API_KEY='+key+'\n', encoding='utf-8')
        opener = Opener(error=HTTPError('https://example.test', 401, key, {},
            io.BytesIO(json.dumps({'message': 'rejected '+key}).encode())))
        output = self.project_root / 'report.json'
        with patch.object(f, 'build_opener', return_value=opener):
            report = f.run(output=output, yahoo_source=f.HERE/'annual_source_20251030_20261029.json',
                           environ={}, full_range=True)
        self.assertTrue(report['api_key_present'])
        self.assertEqual(report['status'], 'DIAGNOSTIC_FAILURE')
        self.assertEqual(report['diagnostics'][0]['http_status'], 401)
        self.assertEqual(report['diagnostics'][1]['status'], 'NOT_RUN_PREVIOUS_FAILURE')
        self.assertEqual(report['full_range_results'], [])
        self.assertEqual(opener.request.get_header('X-api-key'), key)
        self.assertNotIn(key, output.read_text(encoding='utf-8'))

    def test_dotenv_file_error_is_reported_without_network_or_secret(self):
        (self.project_root / '.env').write_bytes(b'FUGLE_API_KEY=synthetic-file-secret\xff')
        output = self.project_root / 'report.json'
        with patch.object(f, 'build_opener') as opener:
            report = f.run(output=output, yahoo_source=f.HERE/'annual_source_20251030_20261029.json', environ={})
        opener.assert_not_called()
        self.assertEqual(report['status'], 'ENV_FILE_READ_ERROR')
        self.assertEqual(report['diagnostics'][0]['status'], 'NOT_RUN_CLIENT_CONFIGURATION')
        self.assertNotIn('synthetic-file-secret', output.read_text(encoding='utf-8'))

    def test_missing_key_fails_without_connection(self):
        with self.assertRaisesRegex(f.ProbeError, '^MISSING_API_KEY$'):
            f.FugleProbeClient(environ={})

    def test_key_newline_rejected(self):
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_API_KEY_FORMAT$'):
            f.FugleProbeClient(environ={'FUGLE_API_KEY':'abc\r\ndef'})

    def test_quoted_bearer_unicode_and_control_keys_rejected_locally(self):
        for key in ('"abc"', "'abc'", 'Bearer abc', 'abc\x00def', '金鑰'):
            with self.subTest(key=key), self.assertRaisesRegex(f.ProbeError, '^INVALID_API_KEY_FORMAT$'):
                f.FugleProbeClient(environ={'FUGLE_API_KEY':key})

    def test_minimal_daily_then_60_contract(self):
        specs = f.diagnostics_plan()
        self.assertEqual([p['timeframe'] for p in specs], ['D','60','D','60'])
        for request_spec in specs[:2]:
            opener = Opener(payload(request_spec['timeframe'], [f.SMOKE_DAY] if request_spec['timeframe']=='D'
                                    else [f.SMOKE_DAY+'T09:00:00+08:00']))
            client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'test'}, opener=opener)
            client.candles(request_spec)
            query = parse_qs(urlsplit(opener.request.full_url).query)
            expected = {'from':[f.SMOKE_DAY], 'to':[f.SMOKE_DAY]}
            if request_spec['timeframe'] == '60':
                expected['timeframe'] = ['60']
            self.assertEqual(query, expected)
            self.assertEqual(client.last_http_status, 200)

    def test_http_error_preserves_validation_but_redacts_secrets_and_headers(self):
        key = 'synthetic-secret'
        body = json.dumps(dict(statusCode=400, message=[
            'timeframe must be one of the following values: 1, 60, D',
            'X-API-KEY: '+key, 'Authorization: Bearer another-secret',
            'headers={Cookie: session=abc}', 'token=short-secret', 'Authorization:\n multiline-secret',
            'bad credential '+key], error='Bad Request',
            headers={'X-API-KEY':key}, debug='unrelated-secret')).encode()
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':key}, opener=Opener(
            error=HTTPError('https://example.test',400,key,{'Sensitive':'do-not-log'},io.BytesIO(body))))
        with self.assertRaises(f.ProbeError) as caught:
            client.candles(spec())
        error = caught.exception
        self.assertEqual(str(error), 'HTTP_400')
        self.assertEqual(error.http_status, 400)
        self.assertIn('timeframe must be', error.api_error)
        self.assertIn('Bad Request', error.api_error)
        for secret in (key,'another-secret','session=abc','short-secret','unrelated-secret','do-not-log','multiline-secret'):
            self.assertNotIn(secret,error.api_error)

    def test_error_body_is_redacted_before_truncation(self):
        key='synthetic-secret'
        message='x '*495+key+' '+('y '*200)
        safe=f.safe_api_error(json.dumps({'message':message}).encode(),key)
        self.assertLessEqual(len(safe),1000)
        self.assertNotIn('synthetic',safe)

    def test_non_json_nested_and_oversized_errors_are_not_logged(self):
        for body in (b'<html>secret</html>', b'[]', b'{"message":{"headers":"secret"}}',
                     b'{"message":"'+b'secret'*4000+b'"}'):
            self.assertNotIn('secret',f.safe_api_error(body,'key'))

    def test_invalid_date_format_rejected_before_network(self):
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'test'}, opener=Opener(payload()))
        for day in ('20260421','2026-02-30'):
            with self.assertRaisesRegex(f.ProbeError,'^INVALID_REQUEST_DATE$'):
                client.candles(spec(day=day))

    def test_request_header_only_and_daily_unadjusted(self):
        opener = Opener(payload('D'))
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'synthetic-secret'}, opener=opener)
        client.candles(spec('D'))
        request = opener.request
        self.assertIn('adjusted=false', request.full_url)
        self.assertIn('timeframe=D', request.full_url)
        self.assertNotIn('synthetic-secret', request.full_url)
        self.assertEqual(request.get_header('X-api-key'), 'synthetic-secret')

    def test_all_http_error_codes_are_safe_and_distinct(self):
        for code in (302,400,401,403,404,429,500):
            opener = Opener(error=HTTPError('https://example.test',code,'synthetic-secret',{},None))
            client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'synthetic-secret'}, opener=opener)
            with self.assertRaisesRegex(f.ProbeError, f'^HTTP_{code}$'):
                client.candles(spec())

    def test_network_exception_does_not_echo_secret(self):
        opener = Opener(error=RuntimeError('synthetic-secret'))
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'synthetic-secret'}, opener=opener)
        with self.assertRaisesRegex(f.ProbeError, '^NETWORK_TLS_OR_JSON_ERROR$'):
            client.candles(spec())

    def test_redirect_cannot_forward_key(self):
        self.assertIsNone(f.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test'))

    def test_symbol_cannot_change_endpoint(self):
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'test'}, opener=Opener(payload()))
        request = spec()
        request['symbol'] = '../other-host'
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_REQUEST$'):
            client.candles(request)

    def test_whole_calendar_year_request_rejected(self):
        client = f.FugleProbeClient(environ={'FUGLE_API_KEY':'test'}, opener=Opener(payload()))
        request = spec()
        request.update(start='2025-01-01', end='2026-01-01')
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_REQUEST_RANGE$'):
            client.candles(request)

    def test_chunks_cover_all_days_without_overlap_and_under_one_year(self):
        blocks = list(f.chunks(date(2025,5,8),date(2026,10,29)))
        self.assertEqual(blocks[0][0],date(2025,5,8))
        self.assertEqual(blocks[-1][1],date(2026,10,29))
        for i, (start,end) in enumerate(blocks):
            self.assertLess((end-start).days,180)
            if i:
                self.assertEqual((start-blocks[i-1][1]).days,1)

    def test_plan_has_gap_probes_first_and_exact_existing_warmup(self):
        window, probes, full = f.plan()
        self.assertTrue(all(p['purpose']=='YAHOO_GAP' for p in probes[:6]))
        self.assertEqual(str(window.minute_start.date()),'2025-10-13')
        self.assertEqual(str(window.daily_start.date()),'2025-05-08')
        self.assertEqual(len(window.sessions),241)
        for symbol in ('2330','2408'):
            for tf in ('60','D'):
                blocks=[p for p in full if p['symbol']==symbol and p['timeframe']==tf]
                self.assertEqual(blocks[-1]['end'],'2026-10-29')

    def test_minute_lots_to_shares_and_daily_shares_unchanged(self):
        minute = f.normalize(payload(),spec())[0]
        daily = f.normalize(payload('D'),spec('D'))[0]
        self.assertEqual(minute['source_volume'],12)
        self.assertEqual(minute['source_volume_unit'],'lots')
        self.assertEqual(minute['Volume'],12000)
        self.assertEqual(daily['Volume'],12)

    def test_utc_converts_to_taipei_preserving_source_offset(self):
        row=f.normalize(payload(stamps=['2026-02-03T01:00:00Z']),spec())[0]
        self.assertEqual(row['bar_start'],'2026-02-03T09:00:00+08:00')
        self.assertEqual(row['source_timestamp'],'2026-02-03T01:00:00+00:00')

    def test_naive_minute_time_rejected(self):
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_DATE_OR_OHLCV$'):
            f.normalize(payload(stamps=['2026-02-03T09:00:00']),spec())

    def test_out_of_range_or_duplicate_dates_rejected(self):
        with self.assertRaisesRegex(f.ProbeError, '^INVALID_DATE_OR_OHLCV$'):
            f.normalize(payload(stamps=['2026-02-04T09:00:00+08:00']),spec())
        with self.assertRaisesRegex(f.ProbeError, '^DUPLICATE_TIMESTAMP$'):
            f.normalize(payload(stamps=['2026-02-03T09:00:00+08:00']*2),spec())

    def test_zero_volume_is_kept_invalid_price_is_rejected(self):
        data=payload()
        data['data'][0]['volume']=0
        self.assertEqual(f.normalize(data,spec())[0]['Volume'],0)
        for field,value in [('open',None),('high',99),('low',103),('volume',-1),('close',float('nan'))]:
            data=payload()
            data['data'][0][field]=value
            with self.assertRaisesRegex(f.ProbeError, '^INVALID_DATE_OR_OHLCV$'):
                f.normalize(data,spec())

    def test_wrong_symbol_exchange_timeframe_adjusted_rejected(self):
        for field,value in [('symbol','2408'),('exchange','OTC'),('timeframe','1'),('adjusted',True)]:
            data=payload()
            data[field]=value
            with self.assertRaisesRegex(f.ProbeError, '^METADATA_MISMATCH$'):
                f.normalize(data,spec())

    def test_empty_data_is_not_a_success(self):
        data=payload()
        data['data']=[]
        with self.assertRaisesRegex(f.ProbeError, '^EMPTY_DATA$'):
            f.normalize(data,spec())

    def test_extra_auction_is_preserved_and_comparison_flags_grid(self):
        rows=f.normalize(payload(stamps=['2026-02-03T13:30:00+08:00','2026-02-03T13:00:00+08:00']),spec())
        yahoo={('2330','60m'):[dict(rows[0])]}
        result=f.compare(spec(),rows,yahoo)
        self.assertFalse(result['timestamps_equal'])
        self.assertEqual(len(result['native_tail']),2)
        self.assertEqual(rows[1]['bar_start'],'2026-02-03T13:30:00+08:00')

    def test_comparison_calculates_every_ohlcv_delta(self):
        rows=f.normalize(payload(),spec())
        reference=dict(rows[0],Close=101,Volume=11000)
        result=f.compare(spec(),rows,{('2330','60m'):[reference]})['rows'][0]
        self.assertEqual(result['status'],'DIFFERENT')
        self.assertEqual(result['delta_fugle_minus_yahoo'],dict(Open=0,High=0,Low=0,Close=1,Volume=1000))

    def test_unknown_yahoo_gap_not_compared_as_zero(self):
        rows=f.normalize(payload(),spec())
        reference=dict(rows[0],Close=None)
        result=f.compare(spec(),rows,{('2330','60m'):[reference]})['rows'][0]
        self.assertEqual(result['status'],'MISSING_OR_INVALID_REFERENCE')
        self.assertIsNone(result['delta_fugle_minus_yahoo']['Close'])

    def test_missing_key_report_never_calls_client_or_claims_coverage(self):
        class MustNotCall:
            def candles(self, request):
                raise AssertionError('No HTTP permitted without env key')
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            report=f.run(output=output,yahoo_source=f.HERE/'annual_source_20251030_20261029.json',
                         full_range=True,environ={},client=MustNotCall())
            self.assertEqual(report['status'],'NOT_RUN_MISSING_API_KEY')
            self.assertIsNone(report['annual_coverage'])
            self.assertEqual(report['expected_60m_bars'],1265)
            self.assertEqual(report['expected_daily_rows'],361)
            self.assertEqual(len(report['probes']),22)

    def test_auth_failure_stops_subsequent_requests_and_full_range(self):
        class Forbidden:
            calls=0
            def candles(self, request):
                self.calls+=1
                raise f.ProbeError('HTTP_403')
        client=Forbidden()
        with tempfile.TemporaryDirectory() as directory:
            report=f.run(output=Path(directory)/'report.json',yahoo_source=f.HERE/'annual_source_20251030_20261029.json',
                         full_range=True,environ={'FUGLE_API_KEY':'synthetic-secret'},client=client)
            self.assertEqual(client.calls,1)
            self.assertEqual(report['full_range_status'],'NOT_RUN_PROBES_FAILED')
            self.assertNotIn('synthetic-secret',json.dumps(report))

    def test_first_400_stops_diagnostics_and_all_22_probes(self):
        class BadRequest:
            calls=0
            def candles(self, request):
                self.calls+=1
                raise f.ProbeError('HTTP_400',http_status=400,api_error='Invalid timeframe')
        client=BadRequest()
        with tempfile.TemporaryDirectory() as directory:
            report=f.run(output=Path(directory)/'report.json',yahoo_source=f.HERE/'annual_source_20251030_20261029.json',
                         full_range=True,environ={'FUGLE_API_KEY':'test'},client=client)
        self.assertEqual(client.calls,1)
        self.assertEqual(report['status'],'DIAGNOSTIC_FAILURE')
        self.assertEqual(report['diagnostics'][0]['http_status'],400)
        self.assertEqual(report['diagnostics'][0]['api_error'],'Invalid timeframe')
        self.assertEqual(report['diagnostics'][1]['status'],'NOT_RUN_PREVIOUS_FAILURE')
        self.assertTrue(all(r['status']=='NOT_RUN_DIAGNOSTICS_FAILED' for r in report['probes']))
        self.assertEqual(report['full_range_results'],[])

    def successful_client(self, fail_at=None):
        class Client:
            calls=[]
            last_http_status=200
            def candles(self, request):
                self.calls.append(request)
                if len(self.calls)==fail_at:
                    raise f.ProbeError('HTTP_400',http_status=400,api_error='Bad Request')
                tf=request['timeframe']
                data=payload(tf,[request['start']] if tf=='D' else [request['start']+'T09:00:00+08:00'])
                data['symbol']=request['symbol']
                return f.normalize(data,request)
        return Client()

    def run_client(self, client, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return f.run(output=Path(directory)/'report.json',yahoo_source=f.HERE/'annual_source_20251030_20261029.json',
                         environ={'FUGLE_API_KEY':'test'},client=client,**kwargs)

    def test_diagnose_only_never_starts_original_probes(self):
        client=self.successful_client()
        report=self.run_client(client,diagnose_only=True)
        self.assertEqual(len(client.calls),4)
        self.assertEqual(report['status'],'DIAGNOSTICS_PASSED_REVIEW_COMPARISONS')
        self.assertTrue(all(r['status']=='NOT_RUN_DIAGNOSE_ONLY' for r in report['probes']))
        self.assertIsNone(report['annual_coverage'])

    def test_original_22_probes_are_unchanged_after_diagnostics(self):
        client=self.successful_client()
        report=self.run_client(client)
        self.assertEqual(client.calls[4:],f.plan()[1])
        self.assertEqual(len(report['probes']),22)
        self.assertEqual(report['status'],'PROBES_FETCHED_REVIEW_COMPARISONS')

    def test_400_in_original_probes_or_full_range_stops_remaining_calls(self):
        for fail_at in (5,27):
            with self.subTest(fail_at=fail_at):
                client=self.successful_client(fail_at)
                report=self.run_client(client,full_range=True)
                self.assertEqual(len(client.calls),fail_at)
                if fail_at==5:
                    self.assertEqual(report['full_range_results'],[])
                else:
                    self.assertEqual(len(report['full_range_results']),12)
                    self.assertTrue(all(r['status']=='INCOMPLETE_REQUESTS' for r in report['annual_coverage']))

    def test_normalization_failure_retains_successful_http_status(self):
        client=f.FugleProbeClient(environ={'FUGLE_API_KEY':'test'},opener=Opener({'data':[]}))
        report=self.run_client(client)
        self.assertEqual(report['diagnostics'][0]['status'],'METADATA_MISMATCH')
        self.assertEqual(report['diagnostics'][0]['http_status'],200)

    def coverage_fixture(self):
        window, _, _ = f.plan()
        result=[]
        for symbol in ('2330','2408'):
            for tf, start in (('60',window.minute_start.date()),('D',window.daily_start.date())):
                rows=[]
                for day in window.calendar.sessions:
                    if not start <= day <= f.END:
                        continue
                    for hour in ((9,10,11,12,13) if tf=='60' else (0,)):
                        rows.append(dict(bar_start=(pd.Timestamp(day,tz=f.TAIPEI)+pd.Timedelta(hours=hour)).isoformat(),
                            Open=100.,High=103.,Low=99.,Close=102.,Volume=12000.))
                result.append(dict(request=dict(symbol=symbol,timeframe=tf),status='FETCHED',rows=rows))
        return window,result

    def test_full_coverage_passes_existing_validator_with_synthetic_complete_data(self):
        window,result=self.coverage_fixture()
        self.assertTrue(all(row['status']=='EXISTING_VALIDATION_PASSED' for row in f.coverage(window,result)))

    def test_full_coverage_rejects_extra_1330_bar(self):
        window,result=self.coverage_fixture()
        extra=dict(result[0]['rows'][-1],bar_start='2026-10-29T13:30:00+08:00')
        result[0]['rows'].append(extra)
        audit=f.coverage(window,result)[0]
        self.assertEqual(audit['status'],'BAR_GRID_OR_CALENDAR_MISMATCH')
        self.assertEqual(audit['unexpected_60m'],['2026-10-29T13:30:00+08:00'])

    def test_full_coverage_does_not_skip_daily_warmup_gap(self):
        window,result=self.coverage_fixture()
        result[1]['rows']=[row for row in result[1]['rows'] if not row['bar_start'].startswith('2025-08-01')]
        audit=f.coverage(window,result)[0]
        self.assertEqual(audit['status'],'EXISTING_VALIDATION_FAILED')
        self.assertEqual(audit['error_code'],'INCOMPLETE_DAILY')

    def test_full_coverage_closed_zero_quote_only_and_conflict_detection(self):
        window,result=self.coverage_fixture()
        closed=dict(result[1]['rows'][-1],bar_start='2026-10-23T00:00:00+08:00',Volume=0.)
        result[1]['rows'].append(closed)
        audit=f.coverage(window,result)[0]
        self.assertEqual(audit['status'],'EXISTING_VALIDATION_PASSED')
        self.assertEqual(audit['excluded_confirmed_closed_daily_rows'][0]['kind'],'ZERO_VOLUME_QUOTE')
        closed['Volume']=1.
        self.assertEqual(f.coverage(window,result)[0]['status'],'BAR_GRID_OR_CALENDAR_MISMATCH')


if __name__ == '__main__':
    unittest.main()
