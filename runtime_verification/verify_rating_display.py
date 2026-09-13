"""Offline real-2408 comparison and unchanged-core audit."""
import hashlib
import json
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding='utf-8')

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import pandas as pd
from app.stock import _attach_support_resistance
from app.volatility import summarize_volatility
from app.bayesian_support.model import explain_prediction
from app.support_resistance_analysis.formatting import format_support_resistance_output

baseline = json.loads((root/'runtime_verification/rating_baseline_hashes.json').read_text(encoding='utf-8-sig'))
for file in baseline:
    assert hashlib.sha256(Path(file['Path']).read_bytes()).hexdigest().upper() == file['Hash'], file['Path']
history = pd.read_csv(root/'data/backtests/support_events_2408_20260913T085146Z_c1304bd4/history.csv', index_col=0, parse_dates=True)
result = dict(stock_code='2408', **summarize_volatility(history))
_attach_support_resistance(result, history)
sr = result['support_resistance']
selected = sr.get('bayesian_support_selected')
assert selected, 'No current candidate; comparison requires an actual evaluated support'
raw_before = json.dumps(selected['result'], sort_keys=True)
original = {k:v for k,v in sr.items() if not k.startswith('bayesian_')}
before = format_support_resistance_output(original) + (
    f"\n・Bayesian 測試支撐（前日已知）：{selected['support_low']:.2f}～{selected['support_high']:.2f}\n" + explain_prediction(selected['result']))
normal = result['support_resistance_text']
research = format_support_resistance_output(sr, research_mode=True)
assert json.dumps(selected['result'], sort_keys=True) == raw_before
assert selected['display']['rating_strategy'] == 'distribution'
text = '# 2408 輸出比較\n\n使用既有真實日 K，截至 ' + str(history.index[-1].date()) + '。修改前文字由同一份未變動的核心結果與舊 formatter 重建；不是重新 fit。\n\n'
for title, output in [('修改前',before),('修改後 Normal',normal),('修改後 Research',research)]:
    text += '## ' + title + '\n\n```text\n' + output + '\n```\n\n'
(root/'runtime_verification/2408_rating_display_comparison.md').write_text(text,encoding='utf-8')
print(normal)
print('Unchanged posterior:', selected['result']['posterior_success_probability'])
print('Core/model hashes unchanged:', len(baseline))
