from typing import Any

from app.rules.signal_change_rule import SignalChangeRule
from app.rules.rsi_rule import RSIRule
from app.rules.macd_rule import MACDRule
from app.rules.kd_rule import KDRule

class RuleEngine:
    """
    負責執行所有已註冊的規則，
    並將規則結果整合成單一通知判斷。
    """

    def __init__(self):
        self.rules = [
            SignalChangeRule(),
            RSIRule(),
            MACDRule(),
            KDRule(),
        ]

    def evaluate(
    self,
        context: dict[str, Any]
    ) -> dict[str, Any]:
        total_score = 0
        matched_rules = []
        triggered_rules = []
        event_types = []

        should_notify = False

        for rule in self.rules:
            result = rule.evaluate(context)

            if not result["matched"]:
                continue

            total_score += result["score"]

            matched_rules.extend(
                result["messages"]
            )

            if result["notify_trigger"]:
                should_notify = True

                triggered_rules.append(
                    result["rule_name"]
                )

                event_types.append(
                    result["event_type"]
                )

        # 只有真的要通知時，才加入策略原始原因
        if should_notify:
            strategy_reasons = context.get(
                "reasons",
                []
            )

            matched_rules.extend(strategy_reasons)

        if total_score >= 5:
            level = "重要通知"
        elif total_score >= 3:
            level = "注意通知"
        else:
            level = "一般通知"

        return {
            "should_notify": should_notify,
            "event_type": self._build_event_type(
                event_types
            ),
            "score": total_score,
            "level": level,
            "matched_rules": matched_rules,
            "triggered_rules": triggered_rules
        }

    def _build_event_type(self, event_types: list[str]) -> str:
        if not event_types:
            return "無事件"

        unique_event_types = list(dict.fromkeys(event_types))

        return "、".join(unique_event_types)