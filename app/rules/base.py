from abc import ABC, abstractmethod
from typing import Any
from enum import StrEnum


class RuleCategory(StrEnum):
    MOMENTUM = 'MOMENTUM'
    TREND = 'TREND'
    RISK = 'RISK'
    EVENT = 'EVENT'
    CONFIRMATION = 'CONFIRMATION'


def evidence(category, code, reason, directional_score=0):
    """Direction is independent of the existing notification importance score."""
    return dict(category=category, code=code, reason=reason,
                directional_score=directional_score)


class BaseRule(ABC):
    """
    所有通知規則的共同介面。

    每一條規則都必須實作 evaluate()，
    並回傳這條規則的判斷結果。
    """

    name: str = "未命名規則"
    rule_category = RuleCategory.EVENT

    @abstractmethod
    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        pass
