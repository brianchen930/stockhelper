from abc import ABC, abstractmethod
from typing import Any


class BaseRule(ABC):
    """
    所有通知規則的共同介面。

    每一條規則都必須實作 evaluate()，
    並回傳這條規則的判斷結果。
    """

    name: str = "未命名規則"

    @abstractmethod
    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        pass