"""Explicit simulation assumptions; the original app has no fill/cost model."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a monetary value')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Invalid monetary value') from error
    if not result.is_finite():
        raise ValueError('Monetary values must be finite')
    return result


def money(value):
    return decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.
    commission_rate: float = 0.001425
    minimum_commission: float = 20.
    sell_tax_rate: float = 0.003
    share_unit: int = 1

    def __post_init__(self):
        if decimal(self.initial_cash) <= 0 or money(self.initial_cash) <= 0:
            raise ValueError('initial_cash must be positive')
        for name in ('commission_rate', 'sell_tax_rate'):
            if not 0 <= decimal(getattr(self, name)) < 1:
                raise ValueError(f'{name} must be in [0, 1)')
        if decimal(self.minimum_commission) < 0:
            raise ValueError('minimum_commission cannot be negative')
        if type(self.share_unit) is not int or self.share_unit < 1:
            raise ValueError('share_unit must be a positive integer')

    def fee(self, gross):
        # Setting rate=0 and minimum=0 gives a genuinely cost-free test model.
        return money(max(decimal(self.minimum_commission), gross * decimal(self.commission_rate)))

    def amounts(self, price, shares, *, sell=False):
        gross = money(decimal(price) * shares)
        fee = self.fee(gross)
        tax = money(gross * decimal(self.sell_tax_rate)) if sell else Decimal('0.00')
        return gross, fee, tax

    def affordable_shares(self, cash, price):
        """Largest whole share-unit quantity including rounded entry commission."""
        price = decimal(price)
        low, high = 0, int(cash / price) // self.share_unit
        while low < high:
            middle = (low + high + 1) // 2
            gross, fee, _ = self.amounts(price, middle * self.share_unit)
            if gross + fee <= cash:
                low = middle
            else:
                high = middle - 1
        return low * self.share_unit

    def to_dict(self):
        return {key: float(value) if isinstance(value, Decimal) else value
                for key, value in asdict(self).items()}
