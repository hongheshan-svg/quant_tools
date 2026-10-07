"""个股：搜索、日线、新闻公告、AI 诊断、按需补齐日线。"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from loguru import logger

from api.deps import get_config, get_pipeline, get_tasks
from api.tasks import TaskManager, business_result_error
from src.services.pipeline_service import PipelineService
from src.utils.stock_code import bare_code

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search(q: str = Query("", max_length=40), limit: int = Query(15, ge=1, le=50),
           pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.search_stocks(q, limit)


@router.get("/diagnoses")
def list_diagnoses(code: str = Query("", max_length=20), action: str = Query("", max_length=20), days: int = Query(30, ge=0, le=3650),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """诊断历史（新的在前）；days=0 不限时间。"""
    return pipeline.list_diagnoses(code or None, action or None, days, limit, offset)


class DeleteDiagnosesBody(BaseModel):
    ids: list[int] | None = Field(None, min_length=1, max_length=200)
    code: str | None = Field(None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def exactly_one_filter(self):
        if bool(self.ids) == bool(self.code):
            raise ValueError("必须且只能指定诊断 ID 列表或股票代码")
        return self


@router.post("/diagnoses/delete")
def delete_diagnoses(body: DeleteDiagnosesBody, pipeline: PipelineService = Depends(get_pipeline)):
    from src.services.data_query_service import DataQueryService
    return {"deleted": DataQueryService(pipeline.db_path).delete_diagnoses(body.ids, body.code)}


def _get_or_404(pipeline: PipelineService, diagnosis_id: int) -> dict[str, Any]:
    row = pipeline.get_diagnosis(diagnosis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    return row


def _attachment(row: dict[str, Any], ext: str) -> str:
    """Content-Disposition：ASCII 回退文件名 + RFC 5987 的 UTF-8 文件名。"""
    day = (row.get("created_at") or "")[:10]
    filename = f"诊断_{row.get('name') or row['code']}_{row['code']}_{day}.{ext}"
    ascii_name = f"diagnosis_{row['code']}_{day}.{ext}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return _get_or_404(pipeline, diagnosis_id)


@router.get("/diagnoses/{diagnosis_id}/outcomes")
def diagnosis_outcomes(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)):
    _get_or_404(pipeline, diagnosis_id)
    from src.services.outcome_engine import OutcomeEngine
    return OutcomeEngine(pipeline.config).list("diagnosis", diagnosis_id)


@router.delete("/diagnoses/{diagnosis_id}")
def delete_diagnosis(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, bool]:
    if not pipeline.delete_diagnosis(diagnosis_id):
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    return {"ok": True}


class ReassessBody(BaseModel):
    profile: Literal["conservative", "balanced", "aggressive"]
    persist: bool = False


@router.post("/diagnoses/{diagnosis_id}/reassess")
def reassess_diagnosis(diagnosis_id: int, body: ReassessBody, pipeline: PipelineService = Depends(get_pipeline),
                       config: dict[str, Any] = Depends(get_config)) -> dict[str, Any]:
    """按另一种决策风格重新评估一条诊断（只用保存的快照，不调用模型）；persist=true 同时保存为该风格的决策信号。"""
    from src.services.stock_diagnosis import StockDiagnosisService

    if pipeline.get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    result = StockDiagnosisService(config).reassess(diagnosis_id, body.profile)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    if body.persist:
        from src.services.decision_signals import DecisionSignalService

        saved = DecisionSignalService(config).save_reassessed(diagnosis_id, body.profile)
        result = {**result, **saved}
    return result


def _diagnosis_markdown(row: dict[str, Any]) -> tuple[str, str]:
    """返回（标题, markdown 正文）。"""
    from src.services.report_templates import render_report
    from src.services.stock_diagnosis import render_markdown

    title = f"{row['name'] or row['code']}({row['code']}) AI 诊断 · {row['created_at']}"
    try:
        body = render_report("diagnosis", row["result"], render_markdown(row["result"]))
    except (KeyError, TypeError):
        body = f"**诊断记录数据不完整**：{row['result'].get('one_sentence', '')}"
    return title, body


@router.get("/diagnoses/{diagnosis_id}/markdown")
def diagnosis_markdown(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> Response:
    row = _get_or_404(pipeline, diagnosis_id)
    title, body = _diagnosis_markdown(row)
    return Response(f"# {title}\n\n{body}\n", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": _attachment(row, "md")})


@router.get("/diagnoses/{diagnosis_id}/image")
def diagnosis_image(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline), config: dict = Depends(get_config)) -> Response:
    from src.services import report_image

    row = _get_or_404(pipeline, diagnosis_id)
    title, body = _diagnosis_markdown(row)
    try:
        opts = report_image.share_options(config)
        opts["footer"] = opts["footer"] or "AI 诊断仅供参考，不构成投资建议"
        png = report_image.render_markdown_image(title, body, **opts)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(png, media_type="image/png", headers={"Content-Disposition": _attachment(row, "png")})


def _fund(code: str, pipeline: PipelineService) -> dict[str, Any] | None:
    """ETF / 指数的识别结果；指数代码带交易所前缀，不能对它调用 bare_code。"""
    from src.services.fund_registry import resolve_fund
    from src.utils.stock_code import resolve_identity

    code = resolve_identity(code).code

    return resolve_fund(code, pipeline.db_path)


@router.get("/{code}/profile")
def stock_profile(code: str, history_days: int = Query(90, ge=1, le=3650), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """个股研究聚合：行情、最近诊断、决策信号、持仓、盯盘信息（只读本地数据，每块独立给出 status）。"""
    from src.services.stock_profile import StockProfileService

    return StockProfileService(pipeline).build(code, history_days)


@router.get("/{code}/daily")
def daily(code: str, limit: int | None = Query(None, ge=1, le=5000), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    fund = _fund(code, pipeline)
    if fund:
        from src.collectors import fund_data

        try:
            fund_data.ensure_fund_daily(fund["code"], pipeline.db_path)
            fund_data.refresh_recent_fund_daily(fund["code"], pipeline.db_path)
        except Exception as e:
            logger.warning(f"补齐 ETF/指数日线异常 [{fund['code']}]: {e}")
        return fund_data.get_fund_daily(fund["code"], pipeline.db_path, limit)
    from src.services.data_query_service import DataQueryService

    return DataQueryService(pipeline.db_path).get_stock_daily_history(bare_code(code), limit)


@router.post("/{code}/history")
def ensure_history(code: str, name: str = "", refresh: bool = False, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """本地日线不足时联网补齐，返回新写入的根数。"""
    fund = _fund(code, pipeline)
    if fund:
        from src.collectors import fund_data

        try:
            return {"added": fund_data.ensure_fund_daily(fund["code"], pipeline.db_path)}
        except Exception as e:
            logger.warning(f"补齐 ETF/指数日线异常 [{fund['code']}]: {e}")
            return {"added": 0}
    try:
        return {"added": pipeline.ensure_history(bare_code(code), name, refresh=refresh)}
    except Exception as error:
        from src.utils.redaction import redact_text
        raise HTTPException(status_code=502, detail=redact_text(error, 300)) from error


@router.get("/{code}/news")
def news(code: str, refresh: bool = False, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    if _fund(code, pipeline):  # ETF / 指数没有个股新闻和公告
        return {"news": [], "notices": []}
    return pipeline.stock_news(bare_code(code), refresh)


@router.get("/{code}/diagnosis")
def latest_diagnosis(code: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any] | None:
    fund = _fund(code, pipeline)
    return pipeline.latest_diagnosis(fund["code"] if fund else bare_code(code))


@router.get("/{code}/diagnosis-trend")
def diagnosis_trend(code: str, days: int = Query(180, ge=1, le=730), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    """诊断评分走势（时间正序）；指数代码不能调用 bare_code，交给 diagnosis_code 规范。"""
    fund = _fund(code, pipeline)
    return pipeline.diagnosis_trend(fund["code"] if fund else bare_code(code), days)


@router.post("/{code}/diagnosis")
def diagnose(code: str, tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    fund = _fund(code, pipeline)
    code = fund["code"] if fund else bare_code(code)
    return tasks.submit("diagnosis", pipeline.diagnose_stock, code, True, dedupe_key=f"diagnosis:{code}", subject={"codes": [code]},
                        result_error=business_result_error, label=f"AI 诊断 {fund['name'] if fund else code}")


@router.get("/diagnoses/{diagnosis_id}/flow")
@router.get("/diagnoses/{diagnosis_id}/diagnostics")
def diagnosis_flow(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    from src.services.run_diagnostics import snapshot
    return snapshot(_get_or_404(pipeline, diagnosis_id).get("run_log"))
