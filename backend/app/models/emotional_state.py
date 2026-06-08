"""CharacterEmotionalState — Sprint 6.A2 M5.6(2026-05-20)角色情绪链。

每行 = 某角色在某幕末尾的 8 维情绪向量(Plutchik 简化版):
  joy / sadness / anger / fear / surprise / disgust / trust / anticipation
  每维 0-10 整数

生产侧:emotional_state_tracker 每幕 narrator 后调用,LLM 抽每个在场角色的本幕末情绪
消费侧:
  - 下幕 narrator/agent_dialogue prompt 注入"上幕末情绪向量",约束情绪连续性
  - consistency_checker 增加 EMOTIONAL_DISCONTINUITY 违规类型
    (单维度跨幕变化 ≥ 5 档且无剧情铺垫 → critical)

设计起源(Gemini 第二轮评测瑕疵 4):
  刘飞收到"下一个是你"死亡威胁后,下一段立即讨好周梦说要请客吃薯片庆祝"平安无事"
  → fear[第7幕]=9 → joy[第8幕]=7 单维突变 16 档,无铺垫
  → emotional_state 链让 LLM 看到上幕情绪后,不允许突变
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

# 8 维情绪键(Plutchik 简化)
EMOTION_KEYS = (
    "joy", "sadness", "anger", "fear",
    "surprise", "disgust", "trust", "anticipation",
)

# 中文标签(prompt 友好)
EMOTION_LABELS_CN = {
    "joy": "喜悦",
    "sadness": "悲伤",
    "anger": "愤怒",
    "fear": "恐惧",
    "surprise": "惊讶",
    "disgust": "厌恶",
    "trust": "信任",
    "anticipation": "期待",
}

# 单维度突变阈值(无铺垫情况下的"断裂"门槛)
# 跨幕单维变化 ≥ 此值 → consistency_checker 判 EMOTIONAL_DISCONTINUITY
EMOTIONAL_DISCONTINUITY_THRESHOLD = 5


@dataclass
class CharacterEmotionalState:
    id: str
    simulation_id: str
    character_id: str
    scene_index: int
    emotion: dict        # 8 维 dict
    rationale: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CharacterEmotionalState":
        def _safe_emotion(raw: object) -> dict:
            if not raw or not isinstance(raw, str):
                return {k: 0 for k in EMOTION_KEYS}
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    return {k: 0 for k in EMOTION_KEYS}
                # 补齐缺失键
                return {
                    k: int(parsed.get(k, 0))
                    if isinstance(parsed.get(k), (int, float))
                    else 0
                    for k in EMOTION_KEYS
                }
            except (json.JSONDecodeError, TypeError):
                return {k: 0 for k in EMOTION_KEYS}

        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            character_id=row["character_id"],
            scene_index=int(row["scene_index"]),
            emotion=_safe_emotion(row["emotion_json"]),
            rationale=row["rationale"] or "",
            created_at=row["created_at"],
        )

    def to_prompt_line(self, character_name: str = "") -> str:
        """LLM prompt 友好的单行表示。

        例:[第 7 幕末] 刘飞:fear=9, sadness=7, anger=2(收死亡威胁,极度恐惧)
        """
        # 取前 3 高的情绪维度
        sorted_emotions = sorted(
            self.emotion.items(), key=lambda x: -x[1],
        )[:3]
        emo_str = ", ".join(
            f"{EMOTION_LABELS_CN.get(k, k)}={v}"
            for k, v in sorted_emotions
            if v > 0
        )
        name_part = f"{character_name}:" if character_name else ""
        rationale_part = f"({self.rationale[:60]})" if self.rationale else ""
        return f"[第 {self.scene_index + 1} 幕末] {name_part}{emo_str} {rationale_part}"
