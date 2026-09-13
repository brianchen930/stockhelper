from typing import Any

from app.rules.signal_change_rule import SignalChangeRule
from app.rules.rsi_rule import RSIRule
from app.rules.macd_rule import MACDRule
from app.rules.kd_rule import KDRule
from app.rules.support_resistance_rule import SupportResistanceRule

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
        if not context.get("analysis_is_valid", True):
            issues = context.get("data_quality_issues") or ["insufficient_data"]
            events = [
                {
                    "event_type": "data_quality",
                    "category": issue,
                    "from": "valid",
                    "to": "invalid",
                    "score": 0,
                    "notify": False,
                    "message": "最新行情資料不完整",
                }
                for issue in issues
            ]
            return {
                "should_notify": False,
                "market_signal_notify": False,
                "data_alert_notify": False,
                "event_type": "data_quality",
                "score": 0,
                "technical_score": 0,
                "level": "不通知",
                "matched_rules": [],
                "technical_matched_rules": [],
                "triggered_rules": [],
                "market_events": [],
                "data_quality_events": events,
                "system_events": [],
                "has_data_quality_issue": True,
            }

        total_score = 0
        technical_score = 0
        matched_rules = []
        technical_matched_rules = []
        triggered_rules = []
        event_types = []
        market_events = []

        should_notify = False

        for rule in self.rules:
            result = rule.evaluate(context)

            if not result["matched"]:
                continue

            total_score += result["score"]

            matched_rules.extend(
                result["messages"]
            )
            if not isinstance(rule, SignalChangeRule):
                technical_score += result["score"]
                technical_matched_rules.extend(result["messages"])

            market_events.extend(result.get("events") or [{
                "event_type": "market_signal",
                "category": result.get("category", result["event_type"]),
                "from": None,
                "to": "matched",
                "score": result["score"],
                "notify": result["notify_trigger"],
                "message": " ".join(result["messages"]),
            }])

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

        sr = SupportResistanceRule().evaluate(context)
        sr_notify = sr['should_notify']
        if sr_notify:
            triggered_rules.append(SupportResistanceRule.name)
            priority = {'一般通知': 0, '注意通知': 1, '重要通知': 2}
            level = max([level] + [e['level'] for e in sr['notifications']], key=priority.get)

        return {
            "should_notify": should_notify or sr_notify,
            "technical_notify": should_notify,
            "support_resistance_notify": sr_notify,
            "support_resistance_events": sr['events'],
            "support_resistance_notifications": sr['notifications'],
            "support_resistance_state": sr['state'],
            "market_signal_notify": should_notify,
            "data_alert_notify": False,
            "event_type": self._build_event_type(
                event_types + (["support_resistance"] if sr_notify else [])
            ),
            "score": total_score,
            "technical_score": technical_score,
            "level": level,
            "matched_rules": matched_rules,
            "technical_matched_rules": technical_matched_rules,
            "triggered_rules": triggered_rules,
            "market_events": market_events,
            "data_quality_events": [],
            "system_events": [],
            "has_data_quality_issue": False,
        }

    def _build_event_type(self, event_types: list[str]) -> str:
        if not event_types:
            return "無事件"

        unique_event_types = list(dict.fromkeys(event_types))

        return "、".join(unique_event_types)
