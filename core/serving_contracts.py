"""Additive contracts for the scale-to-zero serving control plane."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ServingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServingState(str, Enum):
    COLD = "cold"
    PROVISIONING = "provisioning"
    LOADING = "loading"
    READY = "ready"
    DRAINING = "draining"


ACTIVE_SERVING_STATES = frozenset(
    {
        ServingState.PROVISIONING,
        ServingState.LOADING,
        ServingState.READY,
        ServingState.DRAINING,
    }
)


class ServingWakeRequest(ServingModel):
    model_id: str = Field(default="vf-demo", min_length=1, max_length=128)
    confirm_provider_spend: Literal[True]

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if value.strip() != value or any(character.isspace() for character in value):
            raise ValueError("model_id must be a stable non-whitespace identifier")
        return value


class ServingStatus(ServingModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)
    state: ServingState
    url: str | None = Field(default=None, max_length=2048)
    detail: str = Field(default="", max_length=1000)
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    gpu_model: str | None = Field(default=None, min_length=1, max_length=255)
    hourly_price_usd: float | None = Field(default=None, ge=0)
    cost_accrued_usd: float = Field(default=0.0, ge=0)
    cold_start_seconds: float | None = Field(default=None, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("url")
    @classmethod
    def validate_ready_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("serving URL must use https")
        return value

    @model_validator(mode="after")
    def validate_state_shape(self) -> "ServingStatus":
        if self.state in ACTIVE_SERVING_STATES and self.session_id is None:
            raise ValueError("active serving state requires session_id")
        if self.state is ServingState.READY and self.url is None:
            raise ValueError("ready serving state requires url")
        if self.state is not ServingState.READY and self.url is not None:
            raise ValueError("only ready serving state may expose url")
        for value, name in (
            (self.hourly_price_usd, "hourly_price_usd"),
            (self.cost_accrued_usd, "cost_accrued_usd"),
            (self.cold_start_seconds, "cold_start_seconds"),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        return self


__all__ = [
    "ACTIVE_SERVING_STATES",
    "ServingState",
    "ServingStatus",
    "ServingWakeRequest",
]
