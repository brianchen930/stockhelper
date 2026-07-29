from typing import Any

from app.rules.base import BaseRule


class SignalChangeRule(BaseRule):
    name = "訊號變化規則"

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        current_signal = context["current_signal"]
        current_trend = context["current_trend"]

        previous_signal = context.get("previous_signal")
        previous_trend = context.get("previous_trend")

        messages: list[str] = []
        score = 0
        triggered = False

        # 第一次監控
        if previous_signal is None:
            return {
                "rule_name": self.name,
                "matched": True,
                "notify_trigger": True,
                "score": 1,
                "event_type": "首次監控",
                "messages": [
                    "【訊號變化 +1】首次建立監控狀態。"
                ],
            }

        # 訊號改變
        if current_signal != previous_signal:
            triggered = True
            score += 3

            messages.append(
                f"【訊號變化 +3】訊號由「{previous_signal}」"
                f"變成「{current_signal}」。"
            )

        # 趨勢改變
        if current_trend != previous_trend:
            triggered = True
            score += 2

            messages.append(
                f"【趨勢變化 +2】趨勢由「{previous_trend}」"
                f"變成「{current_trend}」。"
            )

        # 只有發生變化時，才根據目前方向調整重要程度
        if triggered and current_signal == "偏多":
            score += 1

            messages.append(
                "【訊號方向 +1】目前訊號偏多。"
            )

        elif triggered and current_signal == "偏空":
            score += 2

            messages.append(
                "【訊號方向 +2】目前訊號偏空，需提高風險注意程度。"
            )

        return {
            "rule_name": self.name,
            "matched": triggered,
            "notify_trigger": triggered,
            "score": score,
            "event_type": "訊號變化",
            "messages": messages,
        }