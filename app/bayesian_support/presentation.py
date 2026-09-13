"""Render already-computed results, preserving the complete research payload."""
import json

from . import rating as settings
from .rating import rate_support_probability


def zone_overlap_ratio(first, second):
    """Intersection over union; a tiny zone inside a large one is not equivalent."""
    a, b = first['low'], first['high']
    c, d = second['low'], second['high']
    if a > b or c > d:
        return 0.
    union = max(b, d) - min(a, c)
    return max(0., min(b, d) - max(a, c)) / union if union > 0 else float(a == c)


def format_probability_block(candidate, *, research_mode=False, show_percent=None, include_zone=True):
    result = candidate['result']
    display = candidate.get('display') or rate_support_probability(result).to_dict()
    lines = [f"・模型評估支撐：{candidate['support_low']:.2f}～{candidate['support_high']:.2f}"] if include_zone else []
    value = display['rating']
    show_percent = settings.SHOW_BAYESIAN_PERCENT_IN_NORMAL_MODE if show_percent is None else show_percent
    if show_percent and value != '資料不足':
        value += f"（{display['posterior_probability']:.0%}）"
    lines.append('支撐成功機率：' + value)
    if research_mode:
        # No filtering/top-three truncation: bucket counts and skipped reasons survive.
        lines.append('Research / Debug（等級為歷史模型預測的相對位置）')
        percent = lambda p: '資料不足' if p is None else f'{p:.1%}'
        lines.append('Posterior：' + percent(display['posterior_probability']))
        lines.append('Prior：' + percent(result.get('prior_success_probability')))
        lines.append('Rating Strategy：' + display['rating_strategy'])
        lines.append('Rating Thresholds：' + str(display['thresholds']))
        lines.append('Calibration / Model Status：' + display['calibration_status'] + ' / ' + display['model_status'])
        lines.append('完整研究資料（含 Positive / Negative Evidence LR、Used / Skipped Evidence、Training / Bucket Samples）：')
        lines.append(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    elif display['rating_description']:
        lines.append(display['rating_description'])
    return '\n'.join(lines)


def append_probability_output(base_text, sr, *, research_mode=False):
    candidate = sr.get('bayesian_support_selected')
    if not candidate:
        return base_text + ('\n支撐成功機率：資料不足' if sr.get('bayesian_support_status') == 'unavailable' else '')
    if research_mode:
        return base_text + '\n\n' + format_probability_block(candidate, research_mode=True)
    model_zone = dict(low=candidate['support_low'], high=candidate['support_high'])
    # Match only actually displayed active/support zones, never resistance.
    from app.support_resistance_analysis.formatting import select_active_zone
    active = select_active_zone(sr)
    support = min(sr.get('support_zones', []), key=lambda z: abs(z.get('distance_pct', 0)), default=None)
    matches = [(title, zone) for title, zone in [('目前測試區', active), ('最近支撐', support)]
               if zone and zone_overlap_ratio(model_zone, zone) >= settings.ZONE_OVERLAP_THRESHOLD]
    if matches:
        title, zone = max(matches, key=lambda pair: zone_overlap_ratio(model_zone, pair[1]))
        lines = base_text.splitlines()
        for index, line in enumerate(lines):
            if line.startswith('・' + title + '：'):
                # Union is presentation-only. Drop the old distance % because it
                # was computed for the original zone, which remains untouched.
                lines[index] = f"・{title}：{min(zone['low'], model_zone['low']):.2f}～{max(zone['high'], model_zone['high']):.2f}"
                lines.insert(index + 2, format_probability_block(candidate, include_zone=False))
                return '\n'.join(lines)
    return base_text + '\n\n' + format_probability_block(candidate)
