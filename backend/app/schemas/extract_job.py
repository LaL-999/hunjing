"""ExtractJob API schema — Sprint 2.B + 断点续抽 (2.B+)。

POST /api/uploads/{upload_id}/extract        触发(扣 1 次 continuation 配额)
POST /api/extract_jobs/{job_id}/reset        用户主动放弃 / 取消(僵尸态恢复)
POST /api/extract_jobs/{job_id}/resume       用户主动从断点继续(不扣新配额)
GET  /api/extract_jobs/{job_id}              状态轮询(2s)
GET  /api/extract_jobs/{job_id}/stream       SSE 实时事件流
GET  /api/projects/{project_id}/extract_jobs 历史列表
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExtractJobResponse(BaseModel):
    """与 ExtractJob.to_response() + extract_service._attach_runtime_fields 对齐。

    tokens_input/output:中间过程持续累加(每 chunk 完成时 _save_intermediate),
    用于前端 SSE 失败降级 polling 时推断 chunk 进度。

    runtime 派生字段(extract_service._attach_runtime_fields 注入,model 默认 False/0):
      is_alive               当前进程是否有活跃 worker;僵尸态识别用
      resumable              state=failed 且 chunk_results 不空 → 用户可点继续抽取
      completed_chunks_count 已完成块数,用于前端显"可恢复 N 块成本,跳过这些"
    """

    id: str
    upload_id: str
    project_id: str
    state: str
    is_admin_retag: bool
    inferred_type: Optional[str] = None
    inferred_custom_type_name: Optional[str] = None
    inferred_tags: list[str] = []
    characters_count: int
    relationships_count: int
    events_count: int
    skipped_count: int
    tokens_input: int = 0
    tokens_output: int = 0
    cost_yuan: float
    error_message: Optional[str] = None
    started_at: str
    completed_at: Optional[str] = None
    # runtime 派生
    is_alive: bool = False
    resumable: bool = False
    completed_chunks_count: int = 0
