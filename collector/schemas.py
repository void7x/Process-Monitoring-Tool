"""Pydantic request and response contracts for the collector API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MetricName = Literal["cpu_percent", "memory_percent", "memory_rss", "read_bytes", "write_bytes"]
Operator = Literal["gt", "gte", "lt", "lte", "eq"]
Severity = Literal["info", "warning", "critical"]
Action = Literal["none", "webhook", "email"]


class ProcessSample(BaseModel):
    """One observation of one exact process instance."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    pid: int = Field(ge=0, le=4_294_967_295)
    process_name: str = Field(min_length=1, max_length=512)
    username: str | None = Field(default=None, max_length=512)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            if "process_name" not in value and "name" in value:
                value["process_name"] = value["name"]
            if "username" not in value and "user" in value:
                value["username"] = value["user"]
        return value
    create_time: float = Field(gt=0, le=4_102_444_800)  # through 2100
    timestamp: float = Field(gt=0, le=4_102_444_800)
    cpu_percent: float = Field(ge=0, le=1_000)
    memory_percent: float = Field(ge=0, le=100)
    memory_rss: int = Field(ge=0, le=2**63 - 1)
    read_bytes: int = Field(ge=0, le=2**63 - 1)
    write_bytes: int = Field(ge=0, le=2**63 - 1)
    state: str = Field(default="running", max_length=32)

    @field_validator("process_name")
    @classmethod
    def process_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("process_name must not be blank")
        if any(ord(char) < 32 for char in value):
            raise ValueError("process_name must not contain control characters")
        return value.strip()


class IngestRequest(BaseModel):
    """Agent payload. A batch is bounded to protect the collector."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    host: str = Field(min_length=1, max_length=255)
    samples: list[ProcessSample] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_payload_names(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            if "host" not in value and "hostname" in value:
                value["host"] = value["hostname"]
            if "samples" not in value:
                for key in ("processes", "metrics"):
                    if key in value:
                        value["samples"] = value[key]
                        break
        return value

    @field_validator("host")
    @classmethod
    def host_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host must not be blank")
        if any(ord(char) < 32 for char in value):
            raise ValueError("host must not contain control characters")
        return value.strip()


class RuleBase(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    metric: MetricName
    operator: Operator
    threshold: float = Field(ge=0, le=2**63 - 1)
    duration: float = Field(default=0, ge=0, le=86_400)
    consecutive_samples: int = Field(default=1, ge=1, le=10_000)
    action: Action = "none"
    cooldown: float = Field(default=300, ge=0, le=604_800)
    severity: Severity = "warning"
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class RuleCreate(RuleBase):
    rule_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    @model_validator(mode="before")
    @classmethod
    def accept_id_alias(cls, value: Any) -> Any:
        if isinstance(value, dict) and "rule_id" not in value and "id" in value:
            value = dict(value)
            value["rule_id"] = value["id"]
        return value


class RuleUpdate(RuleBase):
    pass


class RuleResponse(RuleBase):
    rule_id: str
    created_at: float
    updated_at: float


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    time: float


class IngestResponse(BaseModel):
    status: Literal["accepted"]
    host: str
    accepted: int
    duplicates: int
    alerts_triggered: int
    received_at: float


class RuleListResponse(BaseModel):
    items: list[RuleResponse]
    total: int


class ErrorResponse(BaseModel):
    error: dict[str, Any]


def utc_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
