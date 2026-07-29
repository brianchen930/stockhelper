from typing import Any

from app.rules.base import BaseRule


class MACDRule(BaseRule):
    name = "MACD 規則"

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        macd = context.get("macd")
        macd_signal = context.get("macd_signal")
        macd_histogram = context.get("macd_histogram")

        previous_macd = context.get("previous_macd")
        previous_macd_signal = context.get(
            "previous_macd_signal"
        )
        previous_macd_histogram = context.get(
            "previous_macd_histogram"
        )

        result = {
            "matched": False,
            "score": 0,
            "messages": [],
            "notify_trigger": False,
            "rule_name": self.name,
            "event_type": "MACD 訊號",
        }

        required_values = [
            macd,
            macd_signal,
            previous_macd,
            previous_macd_signal,
        ]

        if any(
            value is None
            for value in required_values
        ):
            return result

        golden_cross = (
            previous_macd <= previous_macd_signal
            and macd > macd_signal
        )

        death_cross = (
            previous_macd >= previous_macd_signal
            and macd < macd_signal
        )

        if golden_cross:
            result["matched"] = True
            result["score"] += 2

            if macd < 0:
                result["messages"].append(
                    "【MACD +2】在零軸下形成黃金交叉，"
                    "可能出現低檔轉強。"
                )
            else:
                result["messages"].append(
                    "【MACD +2】在零軸上形成黃金交叉，"
                    "多方動能增強。"
                )

        elif death_cross:
            result["matched"] = True
            result["score"] += 2

            if macd > 0:
                result["messages"].append(
                    "【MACD +2】在零軸上形成死亡交叉，"
                    "需注意高檔轉弱。"
                )
            else:
                result["messages"].append(
                    "【MACD +2】在零軸下形成死亡交叉，"
                    "空方動能可能持續。"
                )

        if (
            macd_histogram is not None
            and previous_macd_histogram is not None
        ):
            self._evaluate_histogram(
                result=result,
                current_histogram=macd_histogram,
                previous_histogram=previous_macd_histogram,
            )

        return result

    def _evaluate_histogram(
        self,
        result: dict[str, Any],
        current_histogram: float,
        previous_histogram: float,
    ) -> None:
        if (
            current_histogram > 0
            and current_histogram > previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD +1】柱狀體位於零軸上且持續放大，"
                "多方動能增強。"
            )

        elif (
            current_histogram > 0
            and current_histogram < previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD +1】柱狀體仍在零軸上，但正在縮小，"
                "多方動能減弱。"
            )

        elif (
            current_histogram < 0
            and current_histogram < previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD +1】柱狀體位於零軸下且持續擴大，"
                "空方動能增強。"
            )

        elif (
            current_histogram < 0
            and current_histogram > previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD +1】柱狀體仍在零軸下，但正在縮小，"
                "空方動能減弱。"
            )