from fastapi import FastAPI, HTTPException
from app.position_status import PositionStatus, position_metadata, validate_position_fields
from app.stock import (
    get_stock_price,
    get_stock_history,
    get_stock_indicators,
    get_stock_analysis,
    analyze_watchlist
)
from pydantic import BaseModel, model_validator
from app.database import (
    create_tables,
    add_stock,
    get_all_stocks,
    get_stock,
    update_position,
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
    result = send_discord_message(
        "台股監測助手 Discord 通知測試成功 ✅"
    )

    if not result["success"]:
        return {
            "success": False,
            "message": "Discord 通知發送失敗",
            "status_code": result["status_code"],
            "error": result["error"],
        }

    return {
        "success": True,
        "message": "Discord 通知發送成功"
    }

create_tables()

class PositionFields(BaseModel):
    average_cost: float | None = None
    shares: int | None = None
    entry_date: str | None = None

    @model_validator(mode='before')
    @classmethod
    def validate_raw_fields(cls, values):
        return validate_position_fields(values)


class WatchlistCreate(PositionFields):
    stock_code: str
    stock_name: str | None = None
    position_status: PositionStatus = PositionStatus.WATCHING

    @model_validator(mode='before')
    @classmethod
    def validate_raw_fields(cls, values):
        # Before validators run before Pydantic fills the default status.
        if isinstance(values, dict):
            validate_position_fields({'position_status': PositionStatus.WATCHING, **values})
            return values
        return validate_position_fields(values)


class WatchlistUpdate(PositionFields):
    position_status: PositionStatus | None = None

    @model_validator(mode='before')
    @classmethod
    def validate_patch(cls, values):
        if not isinstance(values, dict) or not values or set(values) - set(cls.model_fields):
            raise ValueError('請提供有效的持倉更新欄位')
        if 'position_status' in values and values['position_status'] is None:
            raise ValueError('position_status 不可為 null')
        return values


@app.patch("/watchlist/{stock_code}")
def update_watchlist_stock(stock_code: str, stock: WatchlistUpdate):
    result = update_position(stock_code, **stock.model_dump(exclude_unset=True))
    if result is None:
        raise HTTPException(status_code=404, detail="股票不存在於觀望清單中")
    return result

@app.post("/watchlist")
def add_to_watchlist(stock: WatchlistCreate):
    result = add_stock(**stock.model_dump())
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
    tracked = get_stock(stock_code) or {}
    result = get_stock_analysis(
        stock_code=stock_code,
        period=period,
        **position_metadata(tracked),
    )

    if result is None:
        return {
            "error": "找不到股票分析資料"
        }

    return result

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
