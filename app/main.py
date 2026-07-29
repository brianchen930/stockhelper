from fastapi import FastAPI
from app.stock import get_stock_price, get_stock_history
from app.stock import (
    get_stock_price,
    get_stock_history,
    get_stock_indicators,
    get_stock_analysis,
    analyze_watchlist
)
from pydantic import BaseModel
from app.database import (
    create_tables,
    add_stock,
    get_all_stocks,
    delete_stock
)
from app.notifier import send_discord_message
from contextlib import asynccontextmanager

from app.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()

    yield

    stop_scheduler()


app = FastAPI(lifespan=lifespan)

@app.post("/notification/test")
def test_notification():
    success = send_discord_message(
        "台股監測助手 Discord 通知測試成功 ✅"
    )

    if not success:
        return {
            "success": False,
            "message": "Discord 通知發送失敗"
        }

    return {
        "success": True,
        "message": "Discord 通知發送成功"
    }

create_tables()

class WatchlistCreate(BaseModel):
    stock_code: str
    stock_name: str | None = None

@app.post("/watchlist")
def add_to_watchlist(stock: WatchlistCreate):
    result = add_stock(stock_code=stock.stock_code, stock_name=stock.stock_name)
    if result is None:
        return {
            "error": "股票已存在於觀望清單中"
        }
    return result

@app.get("/watchlist")
def get_watchlist():
    return get_all_stocks()

@app.delete("/watchlist/{stock_code}")
def remove_from_watchlist(stock_code: str):
    deleted = delete_stock(stock_code=stock_code)
    if not deleted:
        return {
            "error": "股票不存在於觀望清單中"
        }
    return {"message": "股票已從觀望清單中移除"}

@app.get("/")
def home():
    return {
        "message": "台股監測助手啟動成功"
    }


@app.get("/stock/{stock_code}")
def stock(stock_code: str):
    result = get_stock_price(stock_code)

    if result is None:
        return {
            "error": "找不到股票資料"
        }

    return result


@app.get("/stock/{stock_code}/history")
def stock_history(
    stock_code: str,
    period: str = "3mo",
    interval: str = "1d"
):
    result = get_stock_history(
        stock_code=stock_code,
        period=period,
        interval=interval
    )

    if result is None:
        return {
            "error": "找不到股票歷史資料"
        }

    return result

@app.get("/stock/{stock_code}/indicators")
def stock_indicators(
    stock_code: str,
    period: str = "6mo"
):
    result = get_stock_indicators(
        stock_code=stock_code,
        period=period
    )

    if result is None:
        return {
            "error": "找不到技術指標資料"
        }

    return result

@app.get("/stock/{stock_code}/analysis")
def stock_analysis(
    stock_code: str,
    period: str = "6mo"
):
    result = get_stock_analysis(
        stock_code=stock_code,
        period=period
    )

    if result is None:
        return {
            "error": "找不到股票分析資料"
        }

    return result

@app.post("/watchlist")
def create_watchlist_stock(stock: WatchlistCreate):
    result = add_stock(
        stock_code=stock.stock_code,
        stock_name=stock.stock_name
    )

    if result is None:
        return {
            "error": "這檔股票已經在自選股中"
        }

    return {
        "message": "新增成功",
        "stock": result
    } 

@app.get("/watchlist")
def read_watchlist():
    stocks = get_all_stocks()

    return {
        "count": len(stocks),
        "stocks": stocks
    }

@app.delete("/watchlist/{stock_code}")
def remove_watchlist_stock(stock_code: str):
    deleted = delete_stock(stock_code)

    if not deleted:
        return {
            "error": "自選股中找不到這檔股票"
        }

    return {
        "message": "刪除成功",
        "stock_code": stock_code
    }

@app.get("/monitor")
def monitor_watchlist():
    stocks = get_all_stocks()

    if not stocks:
        return {
            "message": "自選股清單目前是空的",
            "count": 0,
            "results": []
        }

    results = analyze_watchlist(stocks)

    success_count = sum(
        1 for result in results
        if result["status"] == "success"
    )

    error_count = len(results) - success_count

    return {
        "count": len(results),
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    }