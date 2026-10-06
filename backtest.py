"""Command-line entry point for historical decision replay."""
import argparse
from pathlib import Path

from app.backtest.runner import BacktestRunner, export_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description='台股助手歷史決策回放（不模擬成交或計算績效）')
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--start', required=True, help='YYYY-MM-DD，含當日')
    parser.add_argument('--end', required=True, help='YYYY-MM-DD，含當日')
    parser.add_argument('--position-status', choices=['WATCHING', 'HOLDING'], default='WATCHING',
                        help='固定觀察／持有情境，不依決策模擬成交')
    parser.add_argument('--output', type=Path, help='輸出 .csv 或 .json；預設寫入 backtest_results/')
    args = parser.parse_args(argv)
    output = args.output or Path('backtest_results') / f'{args.symbol}_{args.start}_{args.end}.csv'
    if output.suffix.lower() not in ('.csv', '.json'):
        parser.error('--output 必須是 .csv 或 .json')
    try:
        rows = BacktestRunner().run(args.symbol, args.start, args.end, position_status=args.position_status)
        export_rows(rows, output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'回測失敗：{error}\n')
    print(f'已回放 {len(rows)} 個交易日，動作改變 {sum(r["action_changed"] for r in rows)} 次。輸出：{output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
