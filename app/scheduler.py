from datetime import datetime
import hashlib
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.stock import analyze_watchlist
from app.notifier import send_discord_message
from app.database import get_all_stocks, update_stock_state
from app.rules import evaluate_notification
from app.analysis_engine import generate_analysis

scheduler = AsyncIOScheduler()

TRADING_START_HOUR = 9
TRADING_START_MINUTE = 0
TRADING_END_HOUR = 13
TRADING_END_MINUTE = 30
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def should_run_monitoring(now: datetime | None = None, force: bool = False) -> bool:
    if force:
        return True

    current_time = now or datetime.now(TAIPEI_TZ)

    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=TAIPEI_TZ)

    start_time = current_time.replace(hour=TRADING_START_HOUR, minute=TRADING_START_MINUTE, second=0, microsecond=0)
    end_time = current_time.replace(hour=TRADING_END_HOUR, minute=TRADING_END_MINUTE, second=0, microsecond=0)

    return start_time <= current_time <= end_time


def build_notification_signature(
    current_signal: str,
    current_trend: str,
    score: int,
    matched_rules: list[str],
    summary: str,
) -> str:
    payload = "|".join([
        current_signal,
        current_trend,
        str(score),
        ";".join(sorted(matched_rules)),
        summary.strip(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_monitor_job():
    now = datetime.now(TAIPEI_TZ)

    if not should_run_monitoring(now=now):
        print(f"非交易時間，暫停監控：{now.strftime('%Y-%m-%d %H:%M:%S')}")
        return

    print("=" * 50)
    print(f"開始執行自選股監控：{now}")

    stocks = get_all_stocks()

    if not stocks:
        print("自選股清單目前是空的")
        print("=" * 50)
        return

    results = analyze_watchlist(stocks)

    notification_lines = [
        "🔔 **台股監測助手｜訊號變化**",
        f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ""
    ]

    notification_count = 0

    # 用股票代碼快速取得資料庫中的上次狀態
    stock_state_map = {
        stock["stock_code"]: stock
        for stock in stocks
    }

    for result in results:
        stock_code = result["stock_code"]
        stock_name = result.get("stock_name") or "未知"

        if result["status"] == "error":
            print(
                f"{stock_code} {stock_name}："
                f"分析失敗，{result['error']}"
            )
            continue

        data = result["data"]
        strategy = data["analysis"]

        current_rsi = data.get("rsi")

        current_macd = data.get("macd")
        current_macd_signal = data.get("macd_signal")
        current_macd_histogram = data.get("macd_histogram")

        previous_macd = data.get("previous_macd")
        previous_macd_signal = data.get("previous_macd_signal")
        previous_macd_histogram = data.get("previous_macd_histogram")
        current_kd_k = data.get("kd_k")
        current_kd_d = data.get("kd_d")
        current_kd_j = data.get("kd_j")

        previous_kd_k = data.get("previous_kd_k")
        previous_kd_d = data.get("previous_kd_d")
        previous_kd_j = data.get("previous_kd_j")

        current_signal = strategy["signal"]
        current_trend = strategy["trend"]
        reasons = strategy.get("reasons", [])

        previous_state = stock_state_map.get(stock_code, {})

        rule_result = evaluate_notification(
            current_signal=current_signal,
            current_trend=current_trend,
            previous_signal=previous_state.get("last_signal"),
            previous_trend=previous_state.get("last_trend"),
            reasons=reasons,
            rsi=current_rsi,
            macd=current_macd,
            macd_signal=current_macd_signal,
            macd_histogram=current_macd_histogram,
            previous_macd=previous_macd,
            previous_macd_signal=previous_macd_signal,
            previous_macd_histogram=previous_macd_histogram,
            kd_k=current_kd_k,
            kd_d=current_kd_d,
            kd_j=current_kd_j,
            previous_kd_k=previous_kd_k,
            previous_kd_d=previous_kd_d,
            previous_kd_j=previous_kd_j,
        )

        analysis_result = generate_analysis(
            trend=current_trend,
            signal=current_signal,
            score=rule_result["score"],
            matched_rules=rule_result.get("matched_rules", []),
        )
        current_macd = data.get("macd")
        current_macd_signal = data.get("macd_signal")
        current_macd_histogram = data.get("macd_histogram")

        previous_macd = data.get("previous_macd")
        previous_macd_signal = data.get("previous_macd_signal")
        previous_macd_histogram = data.get("previous_macd_histogram")
        
        realtime_price = data.get("realtime_price")
        realtime_change_percent = data.get("price_change_percent")
        technical_summary = data.get("technical_summary", "資料不足")
        change_percent_text = (
            f"{realtime_change_percent:+.2f}%"
            if realtime_change_percent is not None
            else "資料不足"
        )
        price_label = "即時價" if data.get("price_source") == "realtime" else "收盤價"
        display_price = realtime_price if realtime_price is not None else data["close"]

        print(
            f"{stock_code} {stock_name}｜"
            f"{price_label}：{display_price}｜"
            f"漲跌幅：{change_percent_text}｜"
            f"技術摘要：{technical_summary}｜"
            f"趨勢：{current_trend}｜"
            f"訊號：{current_signal}｜"
            f"RSI：{current_rsi if current_rsi is not None else '資料不足'}｜"
            f"MACD：{current_macd if current_macd is not None else '資料不足'}｜"
            f"訊號線："
            f"{current_macd_signal if current_macd_signal is not None else '資料不足'}｜"
            f"柱狀體："
            f"{current_macd_histogram if current_macd_histogram is not None else '資料不足'}｜"
            f"K：{current_kd_k if current_kd_k is not None else '資料不足'}｜"
            f"D：{current_kd_d if current_kd_d is not None else '資料不足'}｜"
            f"J：{current_kd_j if current_kd_j is not None else '資料不足'}｜"
            f"分數：{rule_result['score']}｜"
            f"等級：{rule_result['level']}｜"
            f"是否通知：{rule_result['should_notify']}"
        )
        
        matched_rules = rule_result.get("matched_rules", [])

        if matched_rules:
            print("  命中規則：")

            for message in matched_rules:
                print(f"    ・{message}")
        else:
            print("  命中規則：無")

        print("  綜合分析：")
        print(f"    ・方向：{analysis_result['market_bias']}")
        print(f"    ・強度：{analysis_result['strength']}")
        print(f"    ・摘要：{analysis_result['summary']}")
        print(f"    ・建議：{analysis_result['suggestion']}")
        print()

        notification_signature = build_notification_signature(
            current_signal=current_signal,
            current_trend=current_trend,
            score=rule_result["score"],
            matched_rules=matched_rules,
            summary=analysis_result["summary"],
        )

        should_send_notification = (
            rule_result["should_notify"]
            and notification_signature != previous_state.get("last_notification_signature")
        )

        if should_send_notification:
            notification_count += 1
            change_percent = data.get("price_change_percent")
            change_text = (
                f"{change_percent:+.2f}%"
                if change_percent is not None
                else "資料不足"
            )

            notification_lines.extend([
                f"**{stock_code} {stock_name}**",
                f"{price_label}：{display_price}",
                f"漲跌幅：{change_text}",
                f"技術摘要：{technical_summary}",
                f"RSI（14）：{current_rsi if current_rsi is not None else '資料不足'}",
                (
                    f"訊號："
                    f"{previous_state.get('last_signal') or '尚未記錄'}"
                    f" → {current_signal}"
                ),
                (
                    f"趨勢："
                    f"{previous_state.get('last_trend') or '尚未記錄'}"
                    f" → {current_trend}"
                ),
                f"通知等級：{rule_result['level']}",
                f"規則分數：{rule_result['score']}",
                f"命中規則：{'；'.join(matched_rules) if matched_rules else '無'}",
                ""
            ])

        update_stock_state(
            stock_code=stock_code,
            signal=current_signal,
            trend=current_trend,
            notified=rule_result["should_notify"],
            notification_signature=notification_signature,
        )

    if notification_count > 0:
        notification_lines.append(
            f"本次共有 {notification_count} 檔股票出現變化。"
        )

        send_discord_message(
            "\n".join(notification_lines)
        )

        print(f"已發送 {notification_count} 檔訊號變化通知")
    else:
        print("本次沒有訊號變化，不發送 Discord 通知")

    print("監控執行完成")
    print("=" * 50)


def start_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        run_monitor_job,
        trigger="interval",
        minutes=1,
        id="watchlist_monitor",
        replace_existing=True
    )

    scheduler.start()
    print("排程器已啟動，每 1 分鐘檢查一次交易時間")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

    print("排程器已停止")
