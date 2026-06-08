"""Consent API schema — /api/consent POST/GET。

对接前端 UploadOverlay 的 consent phase(localStorage huimeng:consent:vX)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ConsentChecks(BaseModel):
    """4 项独立勾选,全部必须 true 才允许提交(前端预校验,后端二次校验)。"""
    adult: bool = Field(..., description="我已年满 18 周岁")
    terms: bool = Field(..., description="同意《用户协议》")
    privacy: bool = Field(..., description="同意《隐私政策》")
    pricing: bool = Field(..., description="同意《服务等级与价格说明》")


class CreateConsentRequest(BaseModel):
    version: str = Field(default="v1", description="协议版本号,与前端 huimeng:consent:vX 同步")
    checks: ConsentChecks


class ConsentRecordResponse(BaseModel):
    id: str
    version: str
    accepted_at: str  # ISO 8601
