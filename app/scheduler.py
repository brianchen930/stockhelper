from datetime import datetime
import hashlib
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.stock import analyze_watchlist
from app.notifier import send_stock_notifications
from app.database import get_all_stocks, save_data_quality_issue, update_stock_state
from app.rules import evaluate_notification
from app.analysis_engine import generate_analysis
from app.analysis.timeframe_summary import format_timeframe_discord
from app.market_data import is_finite_number
from app.data_quality import assess_analysis_quality

scheduler = AsyncIOScheduler()

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DATA_QUALITY_DEBUG_ENABLED = False


def should_run_monitoring(now: datetime | None = None, force: bool = False) -> bool:
    if force:
        return True
    current = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
    if current.weekday() >= 5:
        return False
    market_open = current.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = current.replace(hour=13, minute=30, second=0, microsecond=0)
    return market_open <= current <= market_close


def should_include_timeframe_analysis(analysis_is_valid: bool, timeframe_analysis: object) -> bool:
    return bool(analysis_is_valid and timeframe_analysis)


def build_quality_display_lines(
    analysis_is_valid: bool,
    quality_result: dict,
    stock_code: str,
    stock_name: str,
) -> list[str]:
    issues = quality_result.get("issues") or []
    if not analysis_is_valid:
        return [
            "【資料品質警告】",
            (
                f"{stock_code} {stock_name} 最新行情資料不完整，"
                "本次資料不足，略過完整技術分析，也不觸發股票訊號通知。"
            ),
        ]
    if issues:
        return [
            "【資料品質提醒】",
            f"問題代碼：{', '.join(issues)}",
            "技術分析仍可參考，但本次暫不觸發股票訊號通知。",
        ]
    return []


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

    print("=" * 50)
    print(f"開始執行自選股監控：{now}")

    stocks = get_all_stocks()

    if not stocks:
        print("自選股清單目前是空的")
        print("=" * 50)
        return

    results = analyze_watchlist(stocks)

    notification_header = [
        "🔔 **台股監測助手｜訊號變化**",
        f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ""
    ]

    pending_notifications = []

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
        quality_result = assess_analysis_quality(data)
        analysis_is_valid = quality_result["is_valid"]

        if DATA_QUALITY_DEBUG_ENABLED:
            timeframe_analysis = data.get("timeframe_analysis") or {}
            short_term_result = (timeframe_analysis.get("short_term") or {})
            medium_term_result = (timeframe_analysis.get("medium_term") or {})
            print("=== DATA QUALITY DEBUG ===")
            print("stock_code:", stock_code)
            print("quality_result:", quality_result)
            print("issues:", quality_result.get("issues"))
            print("close:", data.get("close"))
            print("realtime_price:", data.get("realtime_price"))
            print("price_change_percent:", data.get("price_change_percent"))
            print("short_term_label:", short_term_result.get("label"))
            print("medium_term_label:", medium_term_result.get("label"))
            print("short_term_result:", short_term_result)
            print("medium_term_result:", medium_term_result)

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
            analysis_is_valid=analysis_is_valid,
            data_quality_issues=quality_result["issues"],
            macd_analysis=data.get("macd_analysis"),
        )

        analysis_result = generate_analysis(
            trend=current_trend,
            signal=current_signal,
            score=rule_result["technical_score"],
            matched_rules=rule_result.get("technical_matched_rules", []),
            analysis_is_valid=analysis_is_valid,
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
        market_relative_performance = data.get("market_relative_performance") or {}
        data_quality = data.get("data_quality") or {}
        relative_label = market_relative_performance.get("label", "資料不足")
        relative_difference = market_relative_performance.get("difference")
        relative_text = (
            f"{relative_label}（差距 {relative_difference:+.2f}%）"
            if is_finite_number(relative_difference)
            else relative_label
        )
        change_percent_text = (
            f"{realtime_change_percent:+.2f}%"
            if is_finite_number(realtime_change_percent)
            else "資料不足"
        )
        price_label = "即時價" if data.get("price_source") == "realtime" else "收盤價"
        display_price = realtime_price if realtime_price is not None else data["close"]
        display_price_text = display_price if is_finite_number(display_price) else "資料不足"

        print(f"{stock_code} {stock_name}｜{price_label}：{display_price_text}｜漲跌幅：{change_percent_text}")
        print(f"  相對大盤：{relative_text}")
        quality_lines = build_quality_display_lines(
            analysis_is_valid,
            quality_result,
            stock_code,
            stock_name,
        )
        for line in quality_lines:
            print(f"  {line}")
        print("  技術摘要：")
        for item in technical_summary.split(" / "):
            print(f"    ・{item}")
        print(
            f"  通知狀態：股票訊號分數 {rule_result['score']}｜"
            f"等級 {rule_result['level']}｜是否通知 {rule_result['should_notify']}"
        )
        
        matched_rules = rule_result.get("matched_rules", [])

        if matched_rules:
            print("  命中規則：")

            for message in matched_rules:
                print(f"    ・{message}")
        else:
            print("  命中規則：無")

        print("  基礎訊號摘要：")
        print(f"    ・方向：{analysis_result['market_bias']}")
        print(f"    ・基礎訊號強度：{analysis_result['strength']}")
        print(f"    ・摘要：{analysis_result['summary']}")
        timeframe_analysis = data.get("timeframe_analysis")
        if should_include_timeframe_analysis(analysis_is_valid, timeframe_analysis):
            print("  短中期多週期分析：")
            for line in format_timeframe_discord(timeframe_analysis):
                print(f"    {line}")
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
            change_percent = data.get("price_change_percent")
            change_text = (
                f"{change_percent:+.2f}%"
                if is_finite_number(change_percent)
                else "資料不足"
            )
            quality_lines = build_quality_display_lines(
                analysis_is_valid,
                quality_result,
                stock_code,
                stock_name,
            )
            if quality_lines:
                quality_lines = [*quality_lines, ""]

            stock_lines = [
                *notification_header,
                f"**{stock_code} {stock_name}**",
                f"{price_label}：{display_price_text}｜漲跌幅：{change_text}",
                f"相對大盤：{relative_text}",
                "",
                *quality_lines,
                "【技術摘要】",
                *[f"・{item}" for item in technical_summary.split(" / ")],
                "",
                "【命中規則】",
                *([f"・{item}" for item in matched_rules] if matched_rules else ["・無"]),
                "",
                "【基礎訊號摘要】",
                f"方向：{analysis_result['market_bias']}",
                f"基礎訊號強度：{analysis_result['strength']}",
                f"摘要：{analysis_result['summary']}",
                "",
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
                "",
            ]
            if should_include_timeframe_analysis(analysis_is_valid, timeframe_analysis):
                stock_lines.extend(
                    format_timeframe_discord(timeframe_analysis) + [""]
                )
            pending_notifications.append({
                "stock_code": stock_code,
                "stock_name": stock_name,
                "message": "\n".join(stock_lines),
                "signal": current_signal,
                "trend": current_trend,
                "notification_signature": notification_signature,
            })

        if analysis_is_valid:
            if not should_send_notification:
                update_stock_state(
                    stock_code=stock_code,
                    signal=current_signal,
                    trend=current_trend,
                    notified=False,
                    notification_signature=(
                        previous_state.get("last_notification_signature")
                    ),
                )
        else:
            issue_key = f"{stock_code}:{','.join(quality_result['issues'])}"
            save_data_quality_issue(stock_code, issue_key)

    if pending_notifications:
        delivery = send_stock_notifications(pending_notifications)
        delivery_by_code = {
            item["stock_code"]: item
            for item in delivery["item_results"]
        }
        for item in pending_notifications:
            sent = delivery_by_code[item["stock_code"]]["success"]
            update_stock_state(
                stock_code=item["stock_code"],
                signal=item["signal"],
                trend=item["trend"],
                notified=sent,
                notification_signature=(
                    item["notification_signature"] if sent else None
                ),
            )
        print(f"符合通知條件：{delivery['matched_count']} 檔")
        print(f"成功發送：{delivery['success_count']} 檔")
        print(f"發送失敗：{delivery['failed_count']} 檔")
        if delivery["failed_items"]:
            print("失敗項目：")
            for item in delivery["failed_items"]:
                status = item.get("status_code") or "連線錯誤"
                print(
                    f"・{item['stock_code']} {item['stock_name']}："
                    f"Discord {status}｜{item.get('error') or '未知錯誤'}"
                )
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
        minutes=0.5,
        id="watchlist_monitor",
        replace_existing=True
    )

    scheduler.start()
    print("排程器已啟動，每 1 分鐘檢查一次交易時間")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()

    print("排程器已停止")
