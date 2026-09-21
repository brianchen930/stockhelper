"""Short daily context with full details only in debug/research mode."""
import json
from .config import LEVEL_LABELS


def format_context(context, *, debug=False):
    if not context:
        return ''
    day = context.get('latest_available_institutional_date')
    if context.get('freshness') == 'STALE':
        return '\n'.join([f'法人籌碼：資料更新異常（最新僅至 {day}）',
                          f"・{context.get('expected_trade_date')} 法人資料尚未成功取得",
                          '・本次交易建議暫不採用法人籌碼訊號'])
    if context.get('freshness') == 'UNAVAILABLE':
        return '法人籌碼：暫無可用資料\n・本次交易建議暫不採用法人籌碼訊號'
    title = '法人籌碼：' + LEVEL_LABELS[context['institutional_level']]
    if day:
        title += f'（截至 {day}）'
    lines = [title, *('・' + reason for reason in context['reasons'][:3])]
    if context.get('previous_session_context'):
        lines.insert(1, '・今日法人資料尚未公布，目前使用最近交易日資料')
    if debug:
        lines.append('Institutional Debug：' + json.dumps(context, ensure_ascii=False, allow_nan=False))
    return '\n'.join(lines)
