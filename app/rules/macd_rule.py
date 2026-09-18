from typing import Any

from app.rules.base import BaseRule, RuleCategory as C, evidence
from app.macd_analysis import analyze_macd
from app.market_data import is_finite_number


class MACDRule(BaseRule):
    name = "MACD 規則"
    rule_category = C.MOMENTUM

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
            "rule_category": self.rule_category,
            "evidence": [],
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
            not is_finite_number(value)
            for value in required_values
        ):
            return result

        analysis = context.get("macd_analysis") or analyze_macd(
            macd, macd_signal, macd_histogram, previous_macd_histogram,
            previous_macd, previous_macd_signal,
        )
        golden_cross = analysis["cross"] == "golden_cross"
        death_cross = analysis["cross"] == "death_cross"

        if golden_cross:
            result['evidence'].append(evidence(C.MOMENTUM, 'MACD_GOLDEN_CROSS', 'MACD 形成黃金交叉', 2))
            result["matched"] = True
            result["score"] += 2

            if macd < 0:
                result["messages"].append(
                    "【MACD｜多方規則命中】在零軸下形成黃金交叉，"
                    "可能出現低檔轉強。"
                )
            else:
                result["messages"].append(
                    "【MACD｜多方規則命中】在零軸上形成黃金交叉，"
                    "多方動能增強。"
                )

        elif death_cross:
            result['evidence'].append(evidence(C.MOMENTUM, 'MACD_DEATH_CROSS', 'MACD 形成死亡交叉', -2))
            result["matched"] = True
            result["score"] += 2

            if macd > 0:
                result["messages"].append(
                    "【MACD｜空方規則命中】在零軸上形成死亡交叉，"
                    "需注意高檔轉弱。"
                )
            else:
                result["messages"].append(
                    "【MACD｜空方規則命中】在零軸下形成死亡交叉，"
                    "空方動能可能持續。"
                )

        if analysis["momentum"] != "data_insufficient":
            direction = {'bullish_strengthening': 1, 'bullish_weakening': 1,
                         'bearish_strengthening': -1, 'bearish_weakening': -1}.get(analysis['momentum'], 0)
            result['evidence'].append(evidence(C.MOMENTUM, 'MACD_HISTOGRAM',
                'MACD ' + analysis['description'], direction))
            result["matched"] = True
            result["score"] += 1
            category = {
                "bullish_strengthening": "多方規則命中",
                "bullish_weakening": "多方動能轉弱",
                "bearish_strengthening": "空方規則命中",
                "bearish_weakening": "空方動能減弱",
                "neutral": "動能持平",
            }[analysis["momentum"]]
            result["messages"].append(
                f"【MACD｜{category}｜{analysis['label']}】{analysis['description']}"
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
                "【MACD｜多方規則命中】柱狀體位於零軸上且持續放大，"
                "多方動能增強。"
            )

        elif (
            current_histogram > 0
            and current_histogram < previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD｜多方動能轉弱】柱狀體仍在零軸上，但正在縮小，"
                "多方動能減弱。"
            )

        elif (
            current_histogram < 0
            and current_histogram < previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD｜空方規則命中】柱狀體位於零軸下且持續擴大，"
                "空方動能增強。"
            )

        elif (
            current_histogram < 0
            and current_histogram > previous_histogram
        ):
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【MACD｜空方動能減弱】柱狀體仍在零軸下，但正在縮小，"
                "空方動能減弱。"
            )
