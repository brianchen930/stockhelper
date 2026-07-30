from typing import Any

from app.rules.base import BaseRule


class RSIRule(BaseRule):
    name = "RSI 規則"

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        rsi = context.get("rsi")
        current_signal = context.get("current_signal")

        result = {
            "rule_name": self.name,
            "matched": False,
            "notify_trigger": False,
            "score": 0,
            "event_type": "RSI",
            "messages": [],
        }

        if rsi is None:
            return result

        result["matched"] = True

        if rsi >= 80:
            if current_signal == "偏多":
                result["score"] -= 2

                result["messages"].append(
                    f"【RSI｜風險調整 -2】RSI 為 {rsi:.2f}，市場非常過熱，"
                    "偏多訊號需注意追高風險。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜風險 0】RSI 為 {rsi:.2f}，市場非常過熱，"
                    "需注意短線拉回風險。"
                )

        elif rsi >= 70:
            if current_signal == "偏多":
                result["score"] -= 1

                result["messages"].append(
                    f"【RSI｜風險調整 -1】RSI 為 {rsi:.2f}，進入超買區，"
                    "偏多訊號可信度降低，不宜直接追價。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜風險 0】RSI 為 {rsi:.2f}，進入超買區，"
                    "需注意高檔震盪或拉回。"
                )

        elif rsi <= 20:
            if current_signal == "偏空":
                result["score"] -= 2

                result["messages"].append(
                    f"【RSI｜風險調整 -2】RSI 為 {rsi:.2f}，市場非常超賣，"
                    "偏空訊號需注意追空風險。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜風險 0】RSI 為 {rsi:.2f}，市場非常超賣，"
                    "跌幅可能較深，但尚未確認止跌。"
                )

        elif rsi <= 30:
            if current_signal == "偏空":
                result["score"] -= 1

                result["messages"].append(
                    f"【RSI｜風險調整 -1】RSI 為 {rsi:.2f}，進入超賣區，"
                    "偏空訊號可信度降低，可能出現反彈。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜風險 0】RSI 為 {rsi:.2f}，進入超賣區，"
                    "需觀察是否出現止跌訊號。"
                )

        else:
            if current_signal in ["偏多", "偏空"]:
                result["score"] += 1

                result["messages"].append(
                    f"【RSI｜訊號確認 +1】RSI 為 {rsi:.2f}，位於正常區間，"
                    "目前訊號未受到極端 RSI 干擾。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜中性 0】RSI 為 {rsi:.2f}，位於正常區間。"
                )

        return result
