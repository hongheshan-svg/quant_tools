"""资讯源、单源拉取及股票/市场/行业范围检索。"""

from typing import Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from api.deps import bad_request, get_config, get_tasks, not_found
from api.tasks import TaskManager, business_result_error
from src.services.intelligence import IntelligenceService
from src.utils.redaction import redact

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class SourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    symbol: str | None = None
    market: Literal["CN"] = "CN"
    sector: str | None = Field(None, max_length=100)


@router.get("/sources")
def sources(config: dict = Depends(get_config)):
    return redact(IntelligenceService(config).sources())


@router.post("/sources")
def add_source(body: SourceBody, config: dict = Depends(get_config)):
    try:
        return IntelligenceService(config).save_source(body.model_dump())
    except ValueError as error:
        raise bad_request(str(error)) from error


@router.put("/sources/{source_id}")
def update_source(source_id: int, body: SourceBody, config: dict = Depends(get_config)):
    try:
        return IntelligenceService(config).save_source(body.model_dump(), source_id)
    except KeyError as error:
        raise not_found("资讯源不存在") from error
    except ValueError as error:
        raise bad_request(str(error)) from error


@router.post("/sources/{source_id}/fetch")
def fetch_source(source_id: int, config: dict = Depends(get_config), tasks: TaskManager = Depends(get_tasks)):
    service = IntelligenceService(config)
    if not any(s["id"] == source_id for s in service.sources()):
        raise not_found("资讯源不存在")
    return tasks.submit("intelligence", service.fetch_source, source_id, result_error=business_result_error,
                        dedupe_key=f"intelligence:{source_id}", label="拉取资讯源")


@router.get("/items")
def items(symbol: str | None = None, market: Literal["CN"] | None = None, sector: str | None = None,
          days: int = Query(7, ge=1, le=365), limit: int = Query(30, ge=1, le=200), config: dict = Depends(get_config)):
    return IntelligenceService(config).items(symbol=symbol, market=market, sector=sector, days=days, limit=limit)
