"""OutlineScene — Sprint 6.A2 M6(2026-05-20)outline 阶段确定的"幕图纸"。

与 simulation_scene 区别:
  - simulation_scene 是"已生成产物",含完整 narrative_segment
  - outline_scene 是"幕的图纸",含 location / events / props / transition

每行 = outline 阶段锁定的一幕完整元数据(全局一致性锚点):
  - scene_summary 本幕概要(1-2 句话)
  - scene_purpose 本幕作用("推进主线" / "引入伏笔" / ...)
  - location 物理位置(LLM 决定,用户可改;narrator 不可改)
  - time_anchor 时间锚
  - characters_present 在场角色 ids
  - key_events 本幕必须发生的关键事件(narrator 必覆盖)
  - key_props 关键道具引入 / 属性确立(锁定后续不许改属性值)
  - transition_from_last 与上幕的物理连接(治空间撕裂)

Sprint 6.A2 MP(M-planner,2026-05-21)新增:
  - tension_percent 0-100 张力百分比(planner 规划,可空)
  - pacing_tempo  'fast' | 'normal' | 'slow' 节奏(planner 规划,可空)

生产侧:outline_generator LLM 一次性生成全部 N 行(MP 后 planner 每幕跑一次回写 tension/pacing)
消费侧:
  - OutlineReviewView 让用户编辑(scene_summary / key_events / location / characters_present)
  - outline_orchestrator 按 scene_index 顺序跑每幕
  - hard_constraints 把 outline 锁定信息 prepend 到 narrator system_prompt
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

OutlineSceneState = Literal["pending", "running", "done", "failed"]
PacingTempo = Literal["fast", "normal", "slow"]


@dataclass
class OutlineScene:
    id: str
    outline_id: str
    scene_index: int
    scene_summary: str
    scene_purpose: str
    location: str
    time_anchor: str
    characters_present: list[str]
    key_events: list[str]
    key_props: list[dict[str, Any]]
    transition_from_last: str
    user_edited: bool
    state: OutlineSceneState
    generated_simulation_scene_id: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str
    # Sprint 6.A2 MP(2026-05-21)— planner 规划
    tension_percent: Optional[int] = None
    pacing_tempo: Optional[PacingTempo] = None
    # SP-6(2026-05-28,migration 071)— 宏观结构标记(三幕)
    # 'act1_setup' / 'act2_confrontation' / 'act3_resolution' / None
    structure_act: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OutlineScene":
        def _safe_list(raw: object) -> list:
            if not raw or not isinstance(raw, str):
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        # MP 新字段兼容老 row(migration 049 之前的 sim 没这两列)
        try:
            tp_raw = row["tension_percent"]
            tension_percent = int(tp_raw) if tp_raw is not None else None
        except (IndexError, KeyError):
            tension_percent = None
        try:
            pt_raw = row["pacing_tempo"]
            pacing_tempo = pt_raw if pt_raw in ("fast", "normal", "slow") else None
        except (IndexError, KeyError):
            pacing_tempo = None

        # SP-6(2026-05-28):structure_act 兜底
        try:
            sa_raw = row["structure_act"]
            structure_act = (
                sa_raw if sa_raw in ("act1_setup", "act2_confrontation", "act3_resolution")
                else None
            )
        except (IndexError, KeyError):
            structure_act = None

        return cls(
            id=row["id"],
            outline_id=row["outline_id"],
            scene_index=int(row["scene_index"]),
            scene_summary=row["scene_summary"] or "",
            scene_purpose=row["scene_purpose"] or "推进主线",
            location=row["location"] or "",
            time_anchor=row["time_anchor"] or "",
            characters_present=_safe_list(row["characters_present_json"]),
            key_events=_safe_list(row["key_events_json"]),
            key_props=_safe_list(row["key_props_json"]),
            transition_from_last=row["transition_from_last"] or "",
            user_edited=bool(row["user_edited"]),
            state=row["state"],
            generated_simulation_scene_id=row["generated_simulation_scene_id"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tension_percent=tension_percent,
            pacing_tempo=pacing_tempo,
            structure_act=structure_act,
        )

    def to_prompt_block(self) -> str:
        """生成给 narrator 的硬铁律 block(本幕图纸)。"""
        events_str = "\n".join(f"    {i+1}. {e}" for i, e in enumerate(self.key_events))
        props_str = ""
        if self.key_props:
            props_lines = []
            for p in self.key_props:
                if not isinstance(p, dict):
                    continue
                name = p.get("name", "未命名道具")
                action = p.get("action", "")
                properties = p.get("properties", {})
                prop_kv = ""
                if isinstance(properties, dict) and properties:
                    prop_kv = " · 属性:" + " / ".join(
                        f"{k}={v!r}" for k, v in properties.items()
                    )
                props_lines.append(f"    · {name}({action}){prop_kv}")
            props_str = "\n".join(props_lines)

        # MP 张力 / 节奏行(可选,只在 planner 规划过才显示)
        tension_line = ""
        if self.tension_percent is not None:
            tempo_label = {
                "fast": "快(节奏紧凑,推进感强)",
                "normal": "中(自然节奏)",
                "slow": "慢(铺垫细腻,细节展开)",
            }.get(self.pacing_tempo or "normal", "中")
            tension_line = (
                f"\n  · 本幕张力 {self.tension_percent}% / 节奏 {tempo_label}"
                f"\n    ⚠ narrator 笔法须匹配上述张力 + 节奏(高张力 = 句短促紧凑 / 低张力 = 长句铺陈细节)"
            )

        block = f"""【本幕图纸 outline_scene #{self.scene_index + 1}/{self.scene_index + 1}】
  · 物理位置:{self.location}(整幕统一,严禁瞬移到别处)
  · 时间锚:{self.time_anchor}
  · 本幕概要:{self.scene_summary}
  · 本幕作用:{self.scene_purpose}{tension_line}
  · 上幕连接:{self.transition_from_last or '(首幕,无 transition)'}
  · 必须发生的关键事件(narrator 必须用**显性对白或明确动作**完成,不许跳过):
{events_str or '    (无)'}
  · 关键道具(属性已锁,严禁覆盖):
{props_str or '    (无)'}
  ⚠ 铁律(P5.4 强化 2026-05-27 / P5.5 动作完成 2026-05-28):
    1. 物理位置铁律:本幕只能发生 location 内的事件
    2. key_events 执行铁律:**每条 key_event 必须用显性对白或明确动作完成**。
       允许用留白 / 环境描写 / 心理描写烘托气氛,但**绝对禁止用日常琐事(如抽烟 / 喝啤酒 /
       吃饭闲聊 / 看星星 / 摸硬币)替换或绕过核心冲突**。
       例:若 key_event 是"绿子问渡边是否还爱直子",则**必须出现"直子"二字 + 这个问题的语义**,
           不许只让两人聊"乌冬面 / 电话亭 / 烟"等无关话题搪塞。
    3. **动作完成铁律(治"决定性动作被近似动作偷换")**:
       key_event 里的决定性动作必须**真实执行到位 + 写出来**,严禁用
       "近似动作 / 中止动作 / 情绪到位但动作没做完"偷换 —— 这是最隐蔽的违规,务必杜绝:
       - "埋下头发" ✗ "攥紧重新包好放回口袋"(埋 = 必须有挖坑 / 入土 / 掩埋的完整动作)
       - "烧掉信" ✗ "捏着信站了很久"
       - "推开门走进去" ✗ "在门口停住"
       - "发生关系 / 做爱" ✗ "相拥而眠"(动作降级回避)
       铁则:outline 写明的决定性动作,narrator 必须让它**真实完成**。
       若该动作因内容政策无法直白细写,**也必须用明确的留白后果交代它确实发生了**
       (例:直接跳到动作完成后的结果场景 / 用次日清晨的状态暗示),
       **绝不允许把"已完成"偷换成"未完成 / 中止 / 回避"**。宁可含蓄,不可跳过。
    4. 不许改 key_props 属性值
    5. **审计警示**:本幕未完成 key_events(含动作偷换)→ 正典守护者判 severe_breach
       (扣分最重的级别),且 consistency_checker 程序级会检测出"剧情空心化"→ retry narrator 重写。"""
        return block
