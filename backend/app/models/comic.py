"""Comic 表的 Python 表示 — Sprint D.9 Sprint 1。

ADR v3 §6 数据库 schema 锁定;migration 024 创建。

状态机(对齐 ADR v3 §3.3 工作流):
  queued                创建,瞬时等 worker 接手
  scripting             Agent #2 编剧 跑(主耗时之一)
  extracting_visuals    Agent #5 素材库抽取员 跑(与 scripting 并行)
  style_uploading       等用户上传 3 张参考图(可能停留较久,用户操作驱动)
  style_analyzing       Agent #3 v2 Qwen-VL 视觉 DNA 提取 + DeepSeek 综合 + Seedream 出 5 张
  style_voting          等用户 5 选 1 (可能停留较久,用户操作驱动)
  character_anchoring   Agent #4 角色锚定员 跑(读 character_visuals → descriptor + Seedream 立绘)
  designing             Agent #6 导演 跑(每格分镜 + prompt 组装)
  generating            Agent #7 图像生成 + Agent #8 视觉质检 主循环(180 格)
  composing             Agent #10 排版嵌字 (本地 PIL)
  done                  完成,可阅读 / 导出
  failed                任一 LLM 失败,error_message 落库
  cancelled             用户主动停
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional


COMIC_STATES = (
    "queued",
    "scripting",
    "extracting_visuals",
    "style_uploading",
    "style_analyzing",
    "style_voting",
    "character_anchoring",
    "designing",
    "generating",
    "composing",
    "done",
    "failed",
    "cancelled",
)

TERMINAL_STATES = ("done", "failed", "cancelled")

# Agent #1 总控用:用户操作驱动的态(worker 停在这里等用户)vs 后台 worker 推进的态
USER_DRIVEN_STATES = ("style_uploading", "style_voting", "character_anchoring")


def _safe_json_loads(raw: Optional[str], fallback: Any) -> Any:
    """安全 JSON 解析,损坏返 fallback(对齐 D.7.A 兜底网模式)。"""
    if not raw or not isinstance(raw, str):
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


@dataclass
class Comic:
    id: str
    user_id: str
    name: str
    # 输入源:dict {"type": "internal" | "external", "simulation_ids": [...] OR "upload_ids": [...]}
    source: dict

    # ===== 画风定调员 v2 产物(Agent #3 v2)=====
    style_tag: Optional[str]
    style_anchor_image_url: Optional[str]
    style_candidates: list[dict]              # 5 张候选样张(已解析)
    style_reference_image_urls: list[str]     # v3:用户上传的 3 张参考图
    style_visual_dna: Optional[dict]          # v3:Qwen-VL 视觉 DNA
    style_detailed_prompt: Optional[str]      # v3:DeepSeek 综合后的 200-400 字详细 prompt

    # ===== 一致性 L4 =====
    generation_seed: Optional[int]

    # ===== Agent #2 编剧产物(Sprint 2.A 加,migration 029)=====
    # 整本剧本 JSON:{"title": "...", "total_pages": N, "pages": [{panels: [...]}]}
    # 由 Agent #2 _agent_scripter 生成;Agent #6 导演每格组装 prompt 时拉这字段
    script: Optional[dict]

    # ===== 状态机 =====
    state: str
    progress_percent: int

    # ===== 成本 / 错误 / 时间戳 =====
    cost_yuan: float
    error_message: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None

    # ===== Sprint C.4 AI Planner:目标页数(6-18,由 Planner 推荐 + 用户调整)=====
    # 放在所有 default 字段末尾(dataclass 字段顺序约束:default 字段必须连续在末)
    target_pages: int = 12

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Comic":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            source=_safe_json_loads(row["source_json"], {}),
            style_tag=row["style_tag"],
            style_anchor_image_url=row["style_anchor_image_url"],
            style_candidates=_safe_json_loads(row["style_candidates_json"], []),
            style_reference_image_urls=_safe_json_loads(
                row["style_reference_image_urls_json"], []
            ),
            style_visual_dna=_safe_json_loads(row["style_visual_dna_json"], None),
            style_detailed_prompt=row["style_detailed_prompt"],
            generation_seed=row["generation_seed"],
            script=_safe_json_loads(
                row["script_json"] if "script_json" in row.keys() else None, None
            ),
            state=row["state"],
            progress_percent=row["progress_percent"],
            cost_yuan=row["cost_yuan"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            target_pages=int(row["target_pages"]) if "target_pages" in row.keys() else 12,
        )

    def to_response(self, is_alive: bool = False) -> dict[str, Any]:
        """API 返回口径。

        Args:
            is_alive: zombie 检测派生字段(对齐 canonical_guardian / extract_service 模式)
                      state 是 worker 推进态(非 USER_DRIVEN_STATES 也非 TERMINAL_STATES)
                      + is_alive=False → 后端重启后的僵尸,前端可显"恢复 / 重试"
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "source": self.source,
            "style_tag": self.style_tag,
            "style_anchor_image_url": self.style_anchor_image_url,
            "style_candidates": self.style_candidates,
            "style_reference_image_urls": self.style_reference_image_urls,
            "style_visual_dna": self.style_visual_dna,
            "style_detailed_prompt": self.style_detailed_prompt,
            "generation_seed": self.generation_seed,
            "script": self.script,
            "state": self.state,
            "progress_percent": self.progress_percent,
            "cost_yuan": round(self.cost_yuan, 4),
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "target_pages": self.target_pages,
            "is_alive": is_alive,
        }
