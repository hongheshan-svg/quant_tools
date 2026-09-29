"""模拟盘交易与实盘记账。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.deps import bad_request, get_pipeline, get_tasks
from api.tasks import TaskManager
from api.v1.watchlist import save_upload
from src.services.real_portfolio import RealPortfolioService
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


class ActionPlan(BaseModel):
    cash_per_10: float = Field(0.0, ge=0)
    bonus_per_10: float = Field(0.0, ge=0)
    transfer_per_10: float = Field(0.0, ge=0)
    tax_rate: float = Field(0.0, ge=0, lt=1)


class ActionBody(BaseModel):
    """分红送转：直接填到账（action + cash/shares），或填分红方案（plan，按除权日前持仓计算）。"""
    ex_date: str
    code: str = Field(min_length=1, max_length=40)
    action: str = ""
    cash: float = Field(0.0, ge=0)
    shares: int = Field(0, ge=0)
    plan: ActionPlan | None = None
    note: str = ""


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
async def import_trades(file: UploadFile = File(...), preview: bool = False,
                        pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """导入交割单；preview=true 时只解析预览、不写库。"""
    path = await save_upload(file)
    try:
        if preview:
            return RealPortfolioService(pipeline.config).preview_import(str(path))
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


@router.get("/real/actions")
def list_actions(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return RealPortfolioService(pipeline.config).corporate_actions()


@router.post("/real/actions")
def add_action(body: ActionBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    service = RealPortfolioService(pipeline.config)
    if body.plan is not None:
        result = service.add_corporate_action_by_plan(body.ex_date, body.code, note=body.note, **body.plan.model_dump())
    else:
        result = service.add_corporate_action(body.ex_date, body.code, body.action, cash=body.cash, shares=body.shares, note=body.note)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.delete("/real/actions/{action_id}")
def delete_action(action_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    if not RealPortfolioService(pipeline.config).delete_corporate_action(action_id):
        raise HTTPException(status_code=404, detail="分红送转记录不存在")
    return {"ok": True}
