"""去 IP 化导出 API schema — Sprint 2.E。

POST /api/projects/{id}/de_ip_dictionary     生成 / 重新生成字典
GET  /api/projects/{id}/de_ip_dictionary     拿当前字典(无则 404)
POST /api/simulations/{id}/export            导出 markdown(version: original | de_ip)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeIpDictionaryResponse(BaseModel):
    """对齐 DeIpDictionary.to_response()。"""

    project_id: str
    mapping: dict[str, str]
    notes: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_yuan: float = 0
    created_at: str
    updated_at: str


class ExportSimulationRequest(BaseModel):
    """POST /api/simulations/{id}/export body。

    version='de_ip' 时若项目无字典 → 422 NO_DE_IP_DICTIONARY(前端提示先生成)
    """

    version: Literal["original", "de_ip"] = Field(
        default="original",
        description="导出版本:original=原版(留原作角色名);de_ip=去 IP 版(走字典替换)",
    )


class ExportReplacementEntry(BaseModel):
    """单条替换 trail(给前端展示用 + violation_logs 留底)。"""

    original: str
    replaced: str
    count: int       # 该原名在 narrative 中出现并被替换的次数


class ExportSimulationResponse(BaseModel):
    filename: str               # 'narrative_xxx.md' or 'narrative_xxx_de_ip.md'
    content: str                # markdown 全文
    version: str
    # 仅 de_ip 版返:替换 trail(按 count 降序)
    replacements: Optional[list[ExportReplacementEntry]] = None
    # 替换总次数(便利字段)
    total_replacements: int = 0
