"""Stage 5: confidence gate.

HIGH   (>= T_HIGH)          suggested mapping; one-click clinician confirmation.
MEDIUM (T_LOW <= x < T_HIGH) routed to the clinician review queue.
LOW    (< T_LOW)             "No safe automated match found"; never emitted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Band = Literal["high", "medium", "low"]
DEFAULT_T_HIGH = 0.85
DEFAULT_T_LOW = 0.50


class Thresholds(BaseModel):
    high: float = Field(DEFAULT_T_HIGH, ge=0, le=1)
    low: float = Field(DEFAULT_T_LOW, ge=0, le=1)
    source: str = "default"  # default | calibrated | override

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if self.low > self.high:
            raise ValueError("T_LOW must not exceed T_HIGH")
        return self


def band(confidence: float, t: Thresholds) -> Band:
    if confidence >= t.high:
        return "high"
    if confidence >= t.low:
        return "medium"
    return "low"
