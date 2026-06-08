"""分集标题 + 下集预告 LLM 改写 — 阶段 8.4+ Phase 4(2026-06-08)。

把规则版"第 N 集 · 首场 summary 前 14 字"升级为:
  - **title**:12-18 字钩子型集标(含主角名 + 悬念关键词)
  - **teaser**:40 字以内下集预告(留钩子,不剧透核心反转)

---

设计:
  1. 批量调用 — 1 次 LLM 拿全部集的 title + teaser(N 集 ≤ 50,token 充足)
  2. BYOK 自动 — 走 call_llm_json,user_id 从 ContextVar 读
  3. 桥接 SP-2 角色档(可选) — 让 LLM 知道主角驱动力,标题更准
  4. 失败回退 — LLM 调用失败 / JSON 非法 → 返 {} 让调用方保持规则标题
  5. 异常隔离 — 不抛,只 log

---

LLM 输入契约(批量):
  {
    "drivers_block": "...",      # 桥接角色档(可空)
    "episodes": [
      {"episode_number": 1,
       "scene_summaries": ["首场..", "次场..", ...],  # 该集场景 summary 列表
       "rule_title": "第 1 集 · 林墨初入潘西",
       "cliffhanger_potential": 0.65,    # LLM 知道该集尾钩子强度
       "tension_peak": 0.82},
      ...
    ],
    "is_short_drama": true     # 决定 teaser 强度(短剧要更挑逗)
  }

LLM 输出严格 JSON:
  {
    "episodes": [
      {"episode_number": 1,
       "title": "林墨潜入潘西:首战即遭嘲笑",
       "teaser": "他立誓..."},
      ...
    ]
  }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.db import get_connection
from app.screenplay.services import huimeng_bridge

logger = logging.getLogger(__name__)


_LLM_SYSTEM_PROMPT = """你是顶尖剧集 logline 高手,擅长在 1 行内写出钩住观众的集标 + 下集预告。

输入:
  - episodes: 分集方案,每集附 scene_summaries / cliffhanger_potential / tension_peak
  - drivers_block: 主角驱动力档案(surface_goal / deep_need / arc_from_to),可能为空
  - is_short_drama: true=短剧(2-3 分钟/集,需要更强钩子)/ false=长剧

任务:为每集写:
  1. title:12-18 字
     - 含主角名(从 scene_summaries 提取)
     - 含 1 个悬念关键词 / 反差 / 行动指向(例:"潜入""背叛""碾碎""暴露")
     - 禁用纯概述式("第 1 集 · 林墨进入车间")— 这是规则版的弱标题
     - **不要带"第 N 集"前缀** — 输出纯 logline,调用方拼接

  2. teaser:40 字以内,下集预告
     - 用悬念语气,不剧透核心反转
     - 末集 teaser 给"全剧落幕一句话" 或 "余韵金句",不能"下集见"
     - 短剧(is_short_drama=true) → 更挑逗,可用反问、惊叹号
     - 长剧 → 留白更深,文学感重

输出严格 JSON(无 markdown 围栏):
  {
    "episodes": [
      {"episode_number": 1, "title": "林墨潜入潘西:首战即遭嘲笑",
       "teaser": "他立誓三天内反转工厂格局,但师父早已看穿..."},
      ...
    ]
  }

铁律:
  1. episodes 必须与输入 episode_number 一一对应,不漏不增
  2. 每个 title 不许超 25 字(给前端展示留空间)
  3. 每个 teaser 不许超 60 字
  4. JSON 必须可解析,绝不带 markdown 围栏
  5. 主角名优先用 scene_summaries 中高频出现的名字,而非编造
"""


def write_titles_and_teasers(
    episodes: list[Any],   # list[EpisodeWithMeta] (避免循环 import)
    *,
    user_id: str,
    novel_id: str,
    scenes_yaml: list[dict],
    characters_yaml: list[dict],
    is_short_drama: bool = True,
) -> dict[int, dict[str, str]]:
    """批量调 LLM 给每集写 title + teaser。

    Returns:
        dict {episode_number: {"title": "...", "teaser": "..."}}
        失败时返 {},调用方继续用规则版 title。

    Raises:
        不抛 — 异常隔离铁律,失败 log + return {}
    """
    if not episodes:
        return {}

    # 1. 拉桥接角色档(可选)
    drivers_block = ""
    try:
        # 取所有 scene 出现过的角色名
        all_char_ids: set[str] = set()
        for s in scenes_yaml:
            if isinstance(s, dict):
                for cid in (s.get("characters_present") or []):
                    if isinstance(cid, str):
                        all_char_ids.add(cid)
        id_to_name = {
            c["id"]: c["name"]
            for c in characters_yaml
            if isinstance(c, dict) and c.get("id") and c.get("name")
        }
        char_names = [id_to_name[c] for c in all_char_ids if c in id_to_name]
        if char_names:
            conn = get_connection()
            try:
                drivers_block = huimeng_bridge.get_character_drivers_block(
                    conn, user_id=user_id, novel_id=novel_id,
                    character_names=char_names,
                )
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        logger.warning("title_writer: bridge drivers failed: %s", e)
        drivers_block = ""

    # 2. 构造每集的 scene_summaries
    sid_to_summary = {
        s["id"]: str(s.get("summary") or "")
        for s in scenes_yaml
        if isinstance(s, dict) and s.get("id")
    }
    episode_payload = []
    for ep in episodes:
        summaries = [sid_to_summary.get(sid, "") for sid in ep.scene_ids]
        summaries = [s for s in summaries if s]  # 去空
        episode_payload.append({
            "episode_number": ep.episode_number,
            "scene_summaries": summaries[:8],  # 截短防 token 爆
            "rule_title": ep.title,
            "cliffhanger_potential": round(ep.cliffhanger_potential, 2),
            "tension_peak": round(ep.tension_peak, 2),
        })

    user_input = {
        "drivers_block": drivers_block or "(无桥接角色档,凭 scene summary 推断主角)",
        "episodes": episode_payload,
        "is_short_drama": is_short_drama,
    }

    # 3. 调 LLM
    try:
        from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed

        parsed, _usage = call_llm_json(
            _LLM_SYSTEM_PROMPT,
            user_input,
            max_tokens=3000,
            temperature=0.6,  # 标题要有点创造性,允许多样
            retries=1,
            user_id=user_id,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning("title_writer: LLM failed: %s", e)
        return {}
    except Exception as e:  # noqa: BLE001
        logger.warning("title_writer: unexpected error: %s", e)
        return {}

    # 4. 验证 LLM 输出 + 提取
    if not isinstance(parsed, dict):
        logger.warning("title_writer: LLM root not dict")
        return {}

    eps_out = parsed.get("episodes")
    if not isinstance(eps_out, list):
        logger.warning("title_writer: LLM missing episodes array")
        return {}

    result: dict[int, dict[str, str]] = {}
    for ep_obj in eps_out:
        if not isinstance(ep_obj, dict):
            continue
        en = ep_obj.get("episode_number")
        if not isinstance(en, int):
            continue
        title = str(ep_obj.get("title") or "").strip()
        teaser = str(ep_obj.get("teaser") or "").strip()
        if not title:
            continue
        # 截短保护
        if len(title) > 25:
            title = title[:25]
        if len(teaser) > 60:
            teaser = teaser[:60]
        result[en] = {"title": title, "teaser": teaser}

    return result


def apply_to_episodes(
    episodes: list[Any],
    title_teaser_map: dict[int, dict[str, str]],
    *,
    keep_rule_prefix: bool = True,
) -> None:
    """把 LLM 生成的 title + teaser 应用到 episodes 列表(原地修改)。

    Args:
        keep_rule_prefix: True 则保留"第 N 集 · "前缀,只替换冒号后内容
                         (推荐 — 用户能清楚看到集号)
                         False 则完全替换 title 为 LLM 输出

    任何映射缺失或字段空时,保留 episode 原 title / teaser。
    """
    for ep in episodes:
        m = title_teaser_map.get(ep.episode_number)
        if not m:
            continue
        new_title = m.get("title") or ""
        new_teaser = m.get("teaser") or ""

        if new_title:
            if keep_rule_prefix:
                ep.title = f"第 {ep.episode_number} 集 · {new_title}"
            else:
                ep.title = new_title

        if new_teaser:
            ep.teaser = new_teaser


__all__ = [
    "write_titles_and_teasers",
    "apply_to_episodes",
]
