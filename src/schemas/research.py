"""证据上下文与研究产物；对齐上游语义，保留本地诊断字段。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceStatus = Literal["available", "missing", "not_supported", "fallback", "stale", "estimated", "partial", "fetch_failed"]


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    status: EvidenceStatus
    value: Any = None
    source: str | None = None
    as_of: str | None = None
    missing_reason: str | None = None


class ContextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    status: EvidenceStatus
    items: dict[str, ContextItem] = Field(default_factory=dict)
    source: str | None = None
    as_of: str | None = None
    limitations: list[str] = Field(default_factory=list)


class AnalysisContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    pack_version: Literal["1.0"] = "1.0"
    subject: dict[str, str]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    phase: dict[str, Any] = Field(default_factory=dict)
    blocks: dict[str, ContextBlock] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value):
        from src.utils.stock_code import resolve_identity
        code = value.get("code")
        if not code or resolve_identity(code).code != code:
            raise ValueError("上下文标的必须使用规范代码")
        return value

    @field_validator("blocks")
    @classmethod
    def valid_blocks(cls, value):
        allowed = {"quote", "daily", "technical", "flow", "chips", "earnings", "fundamentals", "market", "news", "notices"}
        if set(value) - allowed:
            raise ValueError("上下文包含未知证据块")
        return value

    def to_safe_dict(self) -> dict[str, Any]:
        from src.utils.redaction import redact
        return redact(self.model_dump(mode="json"))


class ResearchEvidence(BaseModel):
    id: str
    source_type: str
    title: str
    summary: str | None = None
    source: str | None = None
    as_of: str | None = None
    freshness: Literal["fresh", "stale", "unknown"] = "unknown"
    quality_level: Literal["good", "usable", "limited", "poor", "unknown"] = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchThesis(BaseModel):
    direction: Literal["bullish", "bearish", "neutral", "unknown"] = "unknown"
    summary: str = ""
    confidence: float | None = Field(None, ge=0, le=1)
    score: float | None = Field(None, ge=0, le=100)
    horizon: str | None = None
    action: str | None = None
    action_label: str | None = None
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class InvalidationCondition(BaseModel):
    id: str
    category: Literal["price", "volume", "evidence", "market", "time", "data_quality", "manual"]
    description: str
    threshold: float | None = None
    severity: Literal["watch", "warning", "critical"] = "warning"


class ResearchArtifact(BaseModel):
    schema_version: Literal["research-artifact-v1"] = "research-artifact-v1"
    artifact_id: str
    source_report_id: int | None = None
    created_at: str | None = None
    subject: dict[str, str]
    thesis: ResearchThesis
    strategy_synthesis: dict[str, Any] = Field(default_factory=dict)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    invalidation_conditions: list[InvalidationCondition] = Field(min_length=1)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    data_quality: dict[str, Any] = Field(default_factory=dict)
