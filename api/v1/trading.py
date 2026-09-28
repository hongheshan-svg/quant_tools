"""模拟盘交易与实盘记账。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from api.deps import bad_request, get_pipeline, get_tasks
from api.tasks import TaskManager
from api.v1.watchlist import save_upload
from src.services.pipeline_service import PipelineService

router = APIRouter(tags=["trading"])


# ---------- 模拟盘 ----------

@router.get("/trading")
def trading_snapshot(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.trading_snapshot()


@router.get("/trading/risk")
def trading_risk(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.portfolio_risk("paper")


@router.post("/trading/orders/prepare")
def prepare_orders(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("prepare_orders", pipeline.prepare_orders, dedupe_key="prepare_orders", label="生成订单")


@router.post("/trading/orders/{order_id}/confirm")
def confirm_order(order_id: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.confirm_order(order_id)


@router.post("/trading/orders/{order_id}/cancel")
def cancel_order(order_id: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.cancel_order(order_id)


@router.post("/trading/exits/check")
def check_exits(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("check_exits", pipeline.check_exits, dedupe_key="check_exits", label="检查止损止盈")


# ---------- 实盘记账 ----------

class TradeBody(BaseModel):
    trade_date: str
    code: str = Field(min_length=1, max_length=40)
    side: str
    price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    fee: float = Field(0.0, ge=0)
    trade_time: str = ""
    note: str = ""


class CashBody(BaseModel):
    cash: float = Field(ge=0)


class PlanBody(BaseModel):
    stop_loss: float | None = Field(None, ge=0)
    target_price: float | None = Field(None, ge=0)


@router.get("/real")
def real_portfolio(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.real_portfolio()


@router.post("/real/trades")
def add_trade(body: TradeBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = pipeline.real_add_trade(**body.model_dump())
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.delete("/real/trades/{trade_id}")
def delete_trade(trade_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return {"ok": pipeline.real_delete_trade(trade_id)}


@router.post("/real/trades/import")
async def import_trades(file: UploadFile = File(...), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    path = await save_upload(file)
    try:
        return pipeline.real_import(str(path))
    finally:
        path.unlink(missing_ok=True)


@router.put("/real/cash")
def set_cash(body: CashBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    pipeline.real_set_cash(body.cash)
    return {"ok": True}


@router.put("/real/plans/{code}")
def set_plan(code: str, body: PlanBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    pipeline.real_set_plan(code, body.stop_loss or None, body.target_price or None)
    return {"ok": True}
