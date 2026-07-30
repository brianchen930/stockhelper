from typing import Any

from app.rules.base import BaseRule


class KDRule(BaseRule):
    name = "KD 規則"

    def evaluate(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        current_k = context.get("kd_k")
        current_d = context.get("kd_d")
        current_j = context.get("kd_j")

        previous_k = context.get("previous_kd_k")
        previous_d = context.get("previous_kd_d")

        result = {
            "matched": False,
            "score": 0,
            "messages": [],
            "notify_trigger": False,
            "rule_name": self.name,
            "event_type": "KD 訊號",
        }

        required_values = [
            current_k,
            current_d,
            previous_k,
            previous_d,
        ]

        if any(
            value is None
            for value in required_values
        ):
            return result

        golden_cross = (
            previous_k <= previous_d
            and current_k > current_d
        )

        death_cross = (
            previous_k >= previous_d
            and current_k < current_d
        )

        if golden_cross:
            result["matched"] = True
            result["score"] += 2

            if current_k < 20 and current_d < 20:
                result["messages"].append(
                    "【KD｜多方規則命中】在低檔區形成黃金交叉，"
                    "可能出現超賣反彈。"
                )

            elif current_k > 80 and current_d > 80:
                result["messages"].append(
                    "【KD｜多方規則命中】在高檔區形成黃金交叉，"
                    "短線動能轉強，但須注意追高風險。"
                )

            else:
                result["messages"].append(
                    "【KD｜多方規則命中】形成黃金交叉，短線動能轉強。"
                )

        elif death_cross:
            result["matched"] = True
            result["score"] += 2

            if current_k > 80 and current_d > 80:
                result["messages"].append(
                    "【KD｜空方規則命中】在高檔區形成死亡交叉，"
                    "需注意短線轉弱。"
                )

            elif current_k < 20 and current_d < 20:
                result["messages"].append(
                    "【KD｜空方規則命中】在低檔區形成死亡交叉，"
                    "空方動能可能延續。"
                )

            else:
                result["messages"].append(
                    "【KD｜空方規則命中】形成死亡交叉，短線動能轉弱。"
                )

        self._evaluate_position(
            result=result,
            current_k=current_k,
            current_d=current_d,
            current_j=current_j,
        )

        return result

    def _evaluate_position(
        self,
        result: dict[str, Any],
        current_k: float,
        current_d: float,
        current_j: float | None,
    ) -> None:
        if current_k >= 80 and current_d >= 80:
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【KD｜風險規則命中】K、D 位於高檔超買區，"
                "短線需注意拉回風險。"
            )

        elif current_k <= 20 and current_d <= 20:
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                "【KD｜風險規則命中】K、D 位於低檔超賣區，"
                "跌幅可能較深，但尚未確認止跌。"
            )

        if current_j is None:
            return

        if current_j > 100:
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                f"【KD｜風險規則命中】J 值為 {current_j:.2f}，高於 100，"
                "短線動能偏熱。"
            )

        elif current_j < 0:
            result["matched"] = True
            result["score"] += 1

            result["messages"].append(
                f"【KD｜空方規則命中】J 值為 {current_j:.2f}，低於 0，"
                "短線動能偏弱。"
            )
