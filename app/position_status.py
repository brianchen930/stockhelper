"""User position metadata, independent of market signals and actions."""
from enum import StrEnum


class PositionStatus(StrEnum):
    WATCHING = "WATCHING"
    HOLDING = "HOLDING"


def read_position_status(value=None) -> PositionStatus:
    """Missing legacy metadata means WATCHING; reject other unknown values."""
    return PositionStatus.WATCHING if value is None else PositionStatus(value)


def format_position_status(value=None) -> str:
    labels = {PositionStatus.WATCHING: "觀察中", PositionStatus.HOLDING: "已持有"}
    return f"持倉狀態：{labels[read_position_status(value)]}"


POSITION_FIELDS = ('average_cost', 'shares', 'entry_date')


def validate_position_fields(values):
    """Validate raw inputs without coercing strings or fractional shares."""
    import math
    import re
    from datetime import date
    if not isinstance(values, dict):
        raise ValueError('持倉資料必須為 JSON 物件')
    cost, shares, entry = (values.get(key) for key in POSITION_FIELDS)
    watching = values.get('position_status') == PositionStatus.WATCHING
    minimum = '大於或等於 0' if watching else '大於 0'
    try:
        valid_cost = (type(cost) in (int, float) and math.isfinite(cost)
                      and (cost >= 0 if watching else cost > 0))
    except OverflowError:
        valid_cost = False
    if cost is not None and not valid_cost:
        raise ValueError(f'average_cost 必須為{minimum}的有限數值')
    if shares is not None and (type(shares) is not int
                               or not (0 if watching else 1) <= shares <= 9223372036854775807):
        raise ValueError(f'shares 必須為{minimum}且可儲存於 SQLite 的整數股數')
    if entry is not None:
        if not isinstance(entry, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', entry):
            raise ValueError('entry_date 必須為 YYYY-MM-DD 合法日期')
        date.fromisoformat(entry)
    return values


def position_metadata(values):
    status = read_position_status(values.get('position_status'))
    return dict(position_status=status.value, **{
        key: values.get(key) if status == PositionStatus.HOLDING else None
        for key in POSITION_FIELDS})


def calculate_unrealized_pnl(position, current_price):
    """Display-only gross P/L; no fees or taxes and no decision feedback."""
    import math
    result = dict(unrealized_return_percent=None, unrealized_pnl=None)
    if read_position_status(position.get('position_status')) != PositionStatus.HOLDING:
        return result
    cost, shares = position.get('average_cost'), position.get('shares')
    valid = lambda value: type(value) in (int, float) and math.isfinite(value) and value > 0
    if not valid(current_price) or not valid(cost):
        return result
    rate = (current_price - cost) / cost * 100
    if math.isfinite(rate):
        result['unrealized_return_percent'] = round(rate, 2)
    if type(shares) is int and shares > 0:
        amount = (current_price - cost) * shares
        if math.isfinite(amount):
            result['unrealized_pnl'] = round(amount, 2)
    return result


def format_position(position, current_price=None):
    metadata = position_metadata(position)
    lines = [format_position_status(metadata['position_status'])]
    if metadata['position_status'] == PositionStatus.WATCHING:
        return lines
    if any(metadata[key] is None for key in POSITION_FIELDS):
        lines.append('持倉資料：尚未完整設定')
    if metadata['average_cost'] is not None:
        lines.append(f"平均成本：{metadata['average_cost']:g}")
    if metadata['shares'] is not None:
        lines.append(f"持有股數：{metadata['shares']:,} 股")
    if metadata['entry_date'] is not None:
        lines.append(f"買進日期：{metadata['entry_date']}")
    pnl = calculate_unrealized_pnl(metadata, current_price)
    if pnl['unrealized_return_percent'] is not None:
        lines.append(f"未實現報酬：{pnl['unrealized_return_percent']:+.2f}%")
    if pnl['unrealized_pnl'] is not None:
        text = f"{pnl['unrealized_pnl']:+,.2f}".rstrip('0').rstrip('.')
        lines.append(f'未實現損益：{text} 元')
    return lines
