from typing import Any

from app.rules.base import BaseRule, RuleCategory as C

VALID_SIGNAL_STATES = {"偏多", "觀望", "偏空"}
VALID_TREND_STATES = {"多頭排列", "空頭排列", "均線糾結"}


class SignalChangeRule(BaseRule):
    name = "訊號變化規則"
    rule_category = C.EVENT

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        current_signal = context["current_signal"]
        current_trend = context["current_trend"]

        previous_signal = context.get("previous_signal")
        previous_trend = context.get("previous_trend")

        messages: list[str] = []
        events: list[dict[str, Any]] = []
        score = 0
        triggered = False

        signal_states_are_valid = (
            current_signal in VALID_SIGNAL_STATES
            and previous_signal in VALID_SIGNAL_STATES
        )
        trend_states_are_valid = (
            current_trend in VALID_TREND_STATES
            and previous_trend in VALID_TREND_STATES
        )

        # 第一次監控只適用於有效行情狀態；資料不足不是監控基準。
        if (
            previous_signal is None
            and current_signal in VALID_SIGNAL_STATES
            and current_trend in VALID_TREND_STATES
        ):
            return {
                "rule_category": self.rule_category,
                "rule_name": self.name,
                "matched": True,
                "notify_trigger": True,
                "score": 1,
                "event_type": "market_signal",
                "category": "initial_state",
                "messages": [
                    "【訊號變化｜事件命中】首次建立監控狀態。"
                ],
                "events": [{
                    "event_type": "market_signal",
                    "category": "initial_state",
                    "from": None,
                    "to": current_signal,
                    "score": 1,
                    "notify": True,
                    "message": "首次建立有效市場監控狀態",
                }],
            }

        # 訊號改變
        if signal_states_are_valid and current_signal != previous_signal:
            triggered = True
            score += 3

            messages.append(
                f"【訊號變化｜事件命中】訊號由「{previous_signal}」"
                f"變成「{current_signal}」。"
            )
            events.append({
                "event_type": "market_signal",
                "category": "signal_change",
                "from": previous_signal,
                "to": current_signal,
                "score": 3,
                "notify": True,
                "message": f"訊號由{previous_signal}轉為{current_signal}",
            })

        # 趨勢改變
        if trend_states_are_valid and current_trend != previous_trend:
            triggered = True
            score += 2

            messages.append(
                f"【趨勢變化｜事件命中】趨勢由「{previous_trend}」"
                f"變成「{current_trend}」。"
            )
            events.append({
                "event_type": "market_signal",
                "category": "trend_change",
                "from": previous_trend,
                "to": current_trend,
                "score": 2,
                "notify": True,
                "message": f"趨勢由{previous_trend}轉為{current_trend}",
            })

        # 只有發生變化時，才根據目前方向調整重要程度
        if triggered and current_signal == "偏多":
            score += 1

            messages.append(
                "【訊號方向｜多方】目前訊號偏多。"
            )

        elif triggered and current_signal == "偏空":
            score += 2

            messages.append(
                "【訊號方向｜空方】目前訊號偏空，需提高風險注意程度。"
            )

        return {
            "rule_category": self.rule_category,
            "rule_name": self.name,
            "matched": triggered,
            "notify_trigger": triggered,
            "score": score,
            "event_type": "market_signal",
            "category": "signal_change",
            "messages": messages,
            "events": events,
        }
