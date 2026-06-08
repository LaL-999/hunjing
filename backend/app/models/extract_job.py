"""GraphExtractionJob 表的 Python 表示 — Sprint 2.B + 断点续抽。

字段对齐 backend/migrations/012_extract_jobs.sql + 013_extract_chunk_results.sql。
状态机:
  queued                 创建,等 worker 接手(threading.Thread daemon)
  extracting_graph       全文喂 LLM 抽 entities + relations(单次,build_graph.md prompt)
  generating_characters  逐角色调 LLM 生成完整档案(主角 ≤ 8,character_generator.md prompt)
  inferring_meta         单次 LLM 调用识别 type + tags + custom_type_name(infer_meta.md prompt)
  saving                 batch INSERT characters / relationships / events,回填 projects.type/tags
  done                   全部成功,upload.state → 'ready'
  failed                 任一步失败 / 用户主动 reset,error_message 填,upload.state → 'failed'/'parsed'

派生 runtime 字段(不在 db,由 extract_service._attach_runtime_fields 注入):
  is_alive               当前 backend 进程的 _RUNNING_EXTRACTS 注册表里有 worker
                         (僵尸态识别:db state=extracting_* 但 is_alive=False → backend 重启过)
  resumable              state='failed' 且 chunk_results 有数据 → 用户可点继续抽取
  completed_chunks_count 已完成块数(给前端显"已抽 N / M 块,可恢复 N 块成本")
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


EXTRACT_JOB_STATES = (
    "queued",
    "extracting_graph",
    "entities_pending_review",   # Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核
    "generating_characters",
    "inferring_meta",
    "saving",
    "done",
    "failed",
)
TERMINAL_EXTRACT_STATES = ("done", "failed")


@dataclass
class ExtractJob:
    id: str
    upload_id: str
    user_id: str
    project_id: str
    state: str
    is_admin_retag: bool
    extracted_graph_json: Optional[str]
    inferred_type: Optional[str]
    inferred_custom_type_name: Optional[str]
    inferred_tags_json: Optional[str]
    characters_count: int
    relationships_count: int
    events_count: int
    skipped_count: int
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    error_message: Optional[str]
    started_at: str
    completed_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ExtractJob":
        return cls(
            id=row["id"],
            upload_id=row["upload_id"],
            user_id=row["user_id"],
            project_id=row["project_id"],
            state=row["state"],
            is_admin_retag=bool(row["is_admin_retag"]),
            extracted_graph_json=row["extracted_graph_json"],
            inferred_type=row["inferred_type"],
            inferred_custom_type_name=row["inferred_custom_type_name"],
            inferred_tags_json=row["inferred_tags_json"],
            characters_count=row["characters_count"],
            relationships_count=row["relationships_count"],
            events_count=row["events_count"],
            skipped_count=row["skipped_count"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_yuan=row["cost_yuan"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def to_response(self) -> dict[str, Any]:
        """API 返回口径(不含 runtime 派生字段)。

        runtime 字段 (is_alive / resumable / completed_chunks_count) 由
        extract_service._attach_runtime_fields(conn, job) 注入。该方法返回
        默认值 False/0,让 schema 也能在测试 / debug 场景下直接 dump 不报错。

        why 暴露 tokens_input/output:前端 SSE 失败降级 polling 时,需要观察
        tokens 增长来推断 chunk 进度(state 在 extracting_graph 一停就几分钟,
        没有 tokens 信号 polling 就无法可视化)。cost_yuan 已经暴露,tokens
        数字是更技术中性的进度指标。
        """
        tags: list[str] = []
        if self.inferred_tags_json:
            try:
                parsed = json.loads(self.inferred_tags_json)
                if isinstance(parsed, list):
                    tags = [str(t) for t in parsed]
            except json.JSONDecodeError:
                tags = []
        return {
            "id": self.id,
            "upload_id": self.upload_id,
            "project_id": self.project_id,
            "state": self.state,
            "is_admin_retag": self.is_admin_retag,
            "inferred_type": self.inferred_type,
            "inferred_custom_type_name": self.inferred_custom_type_name,
            "inferred_tags": tags,
            "characters_count": self.characters_count,
            "relationships_count": self.relationships_count,
            "events_count": self.events_count,
            "skipped_count": self.skipped_count,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_yuan": round(self.cost_yuan, 4),
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            # runtime 字段默认值;调用方应通过 _attach_runtime_fields 覆盖
            "is_alive": False,
            "resumable": False,
            "completed_chunks_count": 0,
        }
