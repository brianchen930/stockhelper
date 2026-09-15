"""Short daily context with full details only in debug/research mode."""
import json
from .config import LEVEL_LABELS


def format_context(context, *, debug=False):
    if not context:
        return ''
    day = context.get('latest_available_institutional_date')
    title = '法人籌碼：' + LEVEL_LABELS[context['institutional_level']]
    if day:
        title += f'（截至 {day}）'
    lines = [title, *('・' + reason for reason in context['reasons'][:3])]
    if debug:
        lines.append('Institutional Debug：' + json.dumps(context, ensure_ascii=False, allow_nan=False))
    return '\n'.join(lines)
