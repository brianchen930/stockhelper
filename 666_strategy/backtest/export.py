"""Complete JSON plus separate fills and round-trip CSVs, including zero trades."""
import csv
import json
from pathlib import Path


def export_backtest(result, path):
    path = Path(path)
    if path.suffix.lower() not in ('.json', '.csv'):
        raise ValueError('輸出檔案必須為 .json 或 .csv')
    path.parent.mkdir(parents=True, exist_ok=True)
    paths = dict(json=path.with_suffix('.json'), fills=path.with_suffix('.fills.csv'),
                 trades=path if path.suffix.lower() == '.csv' else path.with_suffix('.trades.csv'))
    paths['json'].write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for name, rows, empty_fields in [
            ('fills', result.fills, ['symbol', 'action', 'execution_time', 'price', 'shares', 'commission', 'transaction_tax', 'net_profit', 'return_percent']),
            ('trades', result.trades, ['symbol', 'entry_time', 'exit_time', 'entry_price', 'exit_price', 'shares', 'entry_commission', 'exit_commission', 'transaction_tax', 'net_profit', 'return_percent'])]:
        with paths[name].open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else empty_fields)
            writer.writeheader()
            writer.writerows(rows)
    return {key: str(value) for key, value in paths.items()}
