from typing import Any

from app.rules.base import BaseRule, RuleCategory as C, evidence
from app.market_data import is_finite_number


class RSIRule(BaseRule):
    name = "RSI 規則"
    rule_category = C.RISK

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        rsi = context.get("rsi")
        current_signal = context.get("current_signal")

        result = {
            "rule_category": self.rule_category,
            "evidence": [],
            "rule_name": self.name,
            "matched": False,
            "notify_trigger": False,
            "score": 0,
            "event_type": "RSI",
            "messages": [],
        }

        if not is_finite_number(rsi):
            return result

        result["matched"] = True
        result['evidence'].append(evidence(
            C.RISK if rsi >= 70 or rsi <= 30 else C.CONFIRMATION,
            'RSI_EXTREME' if rsi >= 70 or rsi <= 30 else 'RSI_NEUTRAL',
            f'RSI {rsi:.2f}，' + ('超買風險' if rsi >= 70 else '超賣但尚未確認止跌' if rsi <= 30 else '中性，未對目前訊號形成明顯干擾')))

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
                    f"【RSI｜中性確認】RSI 為 {rsi:.2f}，位於正常區間，"
                    "目前訊號未受到極端 RSI 干擾。"
                )
            else:
                result["messages"].append(
                    f"【RSI｜中性 0】RSI 為 {rsi:.2f}，位於正常區間。"
                )

        return result
