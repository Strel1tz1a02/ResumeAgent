"""Validated HTTP contracts for the JD import module."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

JDStatus = Literal["analysing", "confirmed"]
RequirementPriority = Literal["required", "preferred", "normal"]


def _strip_required(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class JDRequirementDraft(BaseModel):
    priority: RequirementPriority = "normal"
    content: str
    sort_order: int = Field(default=0, ge=0)

    _validate_content = field_validator("content")(_strip_required)


class JDImportCreate(BaseModel):
    raw_text: str
    source_url: str | None = None
    company: str = ""
    job_name: str = ""
    type: str = ""
    location: str = ""
    status: JDStatus = "analysing"
    requirements: list[JDRequirementDraft] = Field(default_factory=list)

    _validate_raw_text = field_validator("raw_text")(_strip_required)


class JDInformationUpdate(BaseModel):
    company: str | None = None
    job_name: str | None = None
    type: str | None = None
    location: str | None = None
    status: JDStatus | None = None
    expected_revision: int = Field(ge=0)

    @field_validator("status")
    @classmethod
    def reject_null_status(cls, value: JDStatus | None) -> JDStatus | None:
        if value is None:
            raise ValueError("status must not be null")
        return value


class JDRequirementCreate(JDRequirementDraft):
    expected_information_revision: int = Field(ge=0)


class JDRequirementUpdate(BaseModel):
    priority: RequirementPriority | None = None
    content: str | None = None
    sort_order: int | None = Field(default=None, ge=0)
    expected_revision: int = Field(ge=0)
    expected_information_revision: int = Field(ge=0)

    @field_validator("content")
    @classmethod
    def validate_optional_content(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("content must not be null")
        return _strip_required(value)

    @field_validator("priority", "sort_order")
    @classmethod
    def reject_null_requirement_fields(cls, value):
        if value is None:
            raise ValueError("field must not be null")
        return value


class JDOriginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_text: str
    source_url: str | None


class JDRequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    jd_information_id: int
    priority: RequirementPriority
    content: str
    sort_order: int
    revision: int


class JDImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    jd_origin_id: int
    company: str
    job_name: str
    type: str
    location: str
    status: JDStatus
    revision: int
    origin: JDOriginResponse
    requirements: list[JDRequirementResponse]


class JDImportListResponse(BaseModel):
    items: list[JDImportResponse]
    total: int
