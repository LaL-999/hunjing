"""Upload 表的 Python 表示 — Sprint 2.A 中间态文件上传。

字段对齐 backend/migrations/011_uploads.sql。
state 状态机:
  uploaded   接收成功,文件落盘,未解析
  parsed     解析成功,字符数已填(parsed_text_chars 非 NULL)
  rejected   被红旗词拦截或格式拒,error_message 填原因
  extracting 正在抽取图谱(2.B 阶段会推进到这个状态)
  ready      抽取完成,characters/relationships/events 已落项目
  failed     解析或抽取失败,error_message 填原因
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


UPLOAD_STATES = ("uploaded", "parsed", "rejected", "extracting", "ready", "failed")
TERMINAL_UPLOAD_STATES = ("rejected", "ready", "failed")


@dataclass
class Upload:
    id: str
    project_id: str
    user_id: str
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    parsed_text_chars: Optional[int]
    state: str
    error_message: Optional[str]
    uploaded_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Upload":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            filename=row["filename"],
            storage_path=row["storage_path"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            parsed_text_chars=row["parsed_text_chars"],
            state=row["state"],
            error_message=row["error_message"],
            uploaded_at=row["uploaded_at"],
        )

    def to_response(self) -> dict[str, Any]:
        """API 返回口径(不暴露 storage_path / sha256 内部字段)。"""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "parsed_text_chars": self.parsed_text_chars,
            "state": self.state,
            "error_message": self.error_message,
            "uploaded_at": self.uploaded_at,
        }
