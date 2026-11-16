"""python -m 666_strategy.backtest --symbol 2330 --start ... --end ..."""
import argparse
from datetime import date
import json
from pathlib import Path

from .config import BacktestConfig
from .data import SnapshotProvider, fetch_backtest_data
from .export import export_backtest
from .runner import run_backtest


def main(argv=None):
    parser = argparse.ArgumentParser(description='666_strategy 獨立歷史回測：下一個60分K開盤成交')
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--start', required=True, help='YYYY-MM-DD，含當日')
    parser.add_argument('--end', required=True, help='YYYY-MM-DD，含當日，須已收盤')
    parser.add_argument('--initial-cash', type=float, default=1_000_000.)
    parser.add_argument('--commission-rate', type=float, default=0.001425)
    parser.add_argument('--minimum-commission', type=float, default=20.)
    parser.add_argument('--sell-tax-rate', type=float, default=0.003)
    parser.add_argument('--share-unit', type=int, default=1, help='整數股單位；1000 表示整張；預設1股')
    parser.add_argument('--source', choices=('yahoo', 'fugle'), default='yahoo', help='歷史行情來源，預設保留 Yahoo')
    parser.add_argument('--source-snapshot', type=Path, help='使用所選來源的快照，仍嚴格檢查完整區間')
    parser.add_argument('--calendar', type=Path, help='可選：使用者已確認的完整 SessionCalendar JSON，包含暖身與指定區間')
    parser.add_argument('--ca-bundle', type=Path, help='Optional trusted CA PEM; TLS verification stays enabled')
    parser.add_argument('--cache-dir', type=Path, default=Path(__file__).resolve().parents[1] / '.cache')
    parser.add_argument('--output', type=Path, help='.json 或 .csv；另輸出完整JSON、成交及已平倉交易CSV')
    args = parser.parse_args(argv)
    session = None
    try:
        config = BacktestConfig(args.initial_cash, args.commission_rate, args.minimum_commission,
                                args.sell_tax_rate, args.share_unit)
        calendar = None
        if args.calendar:
            from ..history import SessionCalendar
            stored = json.loads(args.calendar.read_text(encoding='utf-8'))
            calendar = SessionCalendar(tuple(date.fromisoformat(day) for day in stored['sessions']),
                date.fromisoformat(stored['start_date']), date.fromisoformat(stored['through_date']),
                stored.get('source', 'caller verified calendar file'))
        if args.source == 'fugle':
            from ..fugle_provider import FugleProvider, FugleSnapshotProvider
            provider = (FugleSnapshotProvider(args.source_snapshot) if args.source_snapshot else
                        FugleProvider(ca_bundle=args.ca_bundle))
        elif args.source_snapshot:
            provider = SnapshotProvider(args.source_snapshot)
        else:
            from ..provider import YFinanceProvider
            if args.ca_bundle:
                if not args.ca_bundle.is_file():
                    raise ValueError('--ca-bundle 必須為有效的既有 PEM 檔')
                from curl_cffi import requests
                session = requests.Session(impersonate='chrome', verify=str(args.ca_bundle))
            import yfinance as yf
            yf.set_tz_cache_location(str(args.cache_dir))  # this CLI process only
            provider = YFinanceProvider(session=session)
        data = fetch_backtest_data(args.symbol, args.start, args.end, provider=provider, calendar=calendar)
        result = run_backtest(data, config=config)
        output = args.output or Path(__file__).resolve().parents[1] / 'backtest_results' / f'{result.symbol}_{args.start}_{args.end}.json'
        paths = export_backtest(result, output)
        print(json.dumps(dict(symbol=result.symbol, start=result.start, end=result.end, summary=result.summary,
            open_position=result.open_position, pending_decision=result.pending_decision, output=paths),
            ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'回測失敗：{error}\n')
    finally:
        if session is not None:
            session.close()


if __name__ == '__main__':
    raise SystemExit(main())
