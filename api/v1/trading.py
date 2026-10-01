"""模拟盘交易与实盘记账。"""

from __future__ import annotations

from typing import Any, Literal

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
    account: str = ""


class CashBody(BaseModel):
    cash: float = Field(ge=0)
    account: str = ""


class AccountBody(BaseModel):
    name: str
    broker: str = ""
    note: str = ""


class AccountUpdate(BaseModel):
    name: str
    broker: str | None = None
    note: str | None = None


class PlanBody(BaseModel):
    account: str = ""
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
    account: str = ""


@router.get("/real")
def real_portfolio(account: str = "", pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """实盘持仓、流水和组合风险；account 为空是全部账户。"""
    return pipeline.real_portfolio(account or None)


@router.get("/real/risk")
def real_risk(account: str = "", pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.portfolio_risk(f"real:{account}" if account else "real")


@router.get("/real/accounts")
def list_accounts(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return RealPortfolioService(pipeline.config).accounts()


@router.post("/real/accounts")
def add_account(body: AccountBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = RealPortfolioService(pipeline.config).add_account(body.name, body.broker, body.note)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.put("/real/accounts/{name}")
def update_account(name: str, body: AccountUpdate, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = RealPortfolioService(pipeline.config).rename_account(name, body.name, body.broker, body.note)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.delete("/real/accounts/{name}")
def delete_account(name: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = RealPortfolioService(pipeline.config).delete_account(name)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.post("/real/trades")
def add_trade(body: TradeBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = pipeline.real_add_trade(**{**body.model_dump(), "account": body.account or None})
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.delete("/real/trades/{trade_id}")
def delete_trade(trade_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return {"ok": pipeline.real_delete_trade(trade_id)}


@router.post("/real/trades/import")
async def import_trades(file: UploadFile = File(...), preview: bool = False, account: str = "",
                        pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """导入交割单；preview=true 时只解析预览、不写库。"""
    path = await save_upload(file)
    try:
        if preview:
            return RealPortfolioService(pipeline.config, account=account or None).preview_import(str(path))
        return pipeline.real_import(str(path), account or None)
    finally:
        path.unlink(missing_ok=True)


@router.put("/real/cash")
def set_cash(body: CashBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    pipeline.real_set_cash(body.cash, body.account or None)
    return {"ok": True}


@router.put("/real/plans/{code}")
def set_plan(code: str, body: PlanBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    pipeline.real_set_plan(code, body.stop_loss or None, body.target_price or None, body.account or None)
    return {"ok": True}


@router.get("/real/actions")
def list_actions(account: str = "", pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return RealPortfolioService(pipeline.config, account=account or None).corporate_actions()


@router.post("/real/actions")
def add_action(body: ActionBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    service = RealPortfolioService(pipeline.config, account=body.account or None)
    if body.plan is not None:
        result = service.add_corporate_action_by_plan(body.ex_date, body.code, note=body.note, **body.plan.model_dump())
    else:
        result = service.add_corporate_action(body.ex_date, body.code, body.action, cash=body.cash, shares=body.shares, note=body.note)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


class CashFlowBody(BaseModel):
    flow_date: str
    direction: Literal["in", "out"]
    amount: float = Field(gt=0)
    note: str = ""
    account: str = ""


@router.get("/real/cash-flows")
def list_cash_flows(account: str = "", pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return RealPortfolioService(pipeline.config, account=account or None).cash_flows()


@router.post("/real/cash-flows")
def add_cash_flow(body: CashFlowBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    result = RealPortfolioService(pipeline.config, account=body.account or None).add_cash_flow(
        body.flow_date, body.direction, body.amount, body.note)
    if not result["ok"]:
        raise bad_request(result["error"])
    return result


@router.delete("/real/cash-flows/{flow_id}")
def delete_cash_flow(flow_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    if not RealPortfolioService(pipeline.config).delete_cash_flow(flow_id):
        raise HTTPException(status_code=404, detail="出入金记录不存在")
    return {"ok": True}


@router.delete("/real/actions/{action_id}")
def delete_action(action_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    if not RealPortfolioService(pipeline.config).delete_corporate_action(action_id):
        raise HTTPException(status_code=404, detail="分红送转记录不存在")
    return {"ok": True}
