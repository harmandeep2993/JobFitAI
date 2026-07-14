# core/config_validator.py
"""
Pydantic validation model for config.yaml.

Validates the config at startup so missing or malformed fields
produce a clear boot-time error instead of an AttributeError
buried somewhere inside a request handler.

Validation is intentionally strict only where correctness matters
at runtime (weights sum, positive limits). Extra keys in config.yaml
are ignored so the file can hold user notes without breaking the app.
"""

import logging
import sys

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# --- Sub-models ---


class LLMConfig(BaseModel):
    model_config = {"extra": "ignore"}

    timeout: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(gt=0)
    resume_max_input_chars: int = Field(gt=0)
    jd_max_input_chars: int = Field(gt=0)


class MatcherWeights(BaseModel):
    """Weights for the scored sections only.

    Experience years and language level are gates (pass/fail), not weighted
    sections - they carry no weight and must not appear here.
    """

    model_config = {"extra": "ignore"}

    required_skills: float = Field(ge=0.0, le=1.0)
    responsibilities: float = Field(ge=0.0, le=1.0)
    preferred_skills: float = Field(ge=0.0, le=1.0)
    education: float = Field(ge=0.0, le=1.0)
    certifications: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "MatcherWeights":
        """Reject configs where weights don't sum to 1.0 (within 0.001 tolerance)."""
        total = round(
            sum(
                [
                    self.required_skills,
                    self.responsibilities,
                    self.preferred_skills,
                    self.education,
                    self.certifications,
                ]
            ),
            6,
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"matcher.weights must sum to 1.0, got {total} -- "
                f"adjust the weights in config.yaml"
            )
        return self


class MatcherThresholds(BaseModel):
    model_config = {"extra": "ignore"}

    excellent: int = Field(ge=1, le=100)
    good: int = Field(ge=1, le=100)
    partial: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> "MatcherThresholds":
        if not (self.excellent > self.good > self.partial):
            raise ValueError(
                f"matcher.thresholds must satisfy excellent > good > partial, "
                f"got {self.excellent} / {self.good} / {self.partial}"
            )
        return self


class MatcherConfig(BaseModel):
    model_config = {"extra": "ignore"}

    weights: MatcherWeights
    thresholds: MatcherThresholds


class ValidatorConfig(BaseModel):
    model_config = {"extra": "ignore"}

    max_file_size_mb: int = Field(gt=0)
    supported_extensions: list[str]

    @model_validator(mode="after")
    def extensions_not_empty(self) -> "ValidatorConfig":
        if not self.supported_extensions:
            raise ValueError("validator.supported_extensions must not be empty")
        return self


class Settings(BaseModel):
    """Top-level validated settings loaded from config.yaml."""

    model_config = {"extra": "ignore"}

    llm_config: LLMConfig
    matcher: MatcherConfig
    validator: ValidatorConfig


# --- Public API ---


def validate_config(raw: dict) -> Settings:
    """
    Validate a raw config dict against the Settings model.

    On validation failure logs each offending field and calls sys.exit(1)
    so the server refuses to start with a broken config rather than failing
    silently on the first real request.
    """
    from pydantic import ValidationError

    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        logger.critical("config.yaml validation failed -- fix before starting:")
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err["loc"]) if err["loc"] else "(root)"
            logger.critical("  [%s] %s", loc, err["msg"])
        sys.exit(1)
