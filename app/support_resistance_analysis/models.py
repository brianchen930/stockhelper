from dataclasses import dataclass, asdict
from typing import Literal


@dataclass(frozen=True)
class Candidate:
    price: float
    method: str
    family: str
    position: int
    confirmed_position: int
    low: float | None = None
    high: float | None = None
    weight: float = 1.0


@dataclass
class SupportResistanceLevel:
    type: Literal['support', 'resistance', 'active']
    low: float
    high: float
    center: float
    strength_score: float
    strength_label: str
    methods: list[str]
    touch_count: int
    distance_pct: float
    last_touch_date: str | None
    last_seen_date: str | None
    method_count: int

    def to_dict(self):
        result = asdict(self)
        # Compatibility with the previous detector's display scripts.
        result.update(zone_low=self.low, zone_high=self.high,
                      sources=self.methods, strength=self.strength_label)
        return result


def date_at(df, position):
    value = df.index[position]
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)
