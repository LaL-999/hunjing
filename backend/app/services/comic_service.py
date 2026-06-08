"""comic_service.py — 漫画态主服务

ADR v3 §3 锁定 10 agent 流水线:
  #1  Orchestrator        本服务(状态机调度 + _RUNNING_COMICS 注册表)
  #2  Screenwriter        _agent_scripter                 prompts/screenwriter.md
  #3  Style Director v2   _agent_style_director_v2        prompts/style_director_v{2,3}.md(Qwen-VL 视觉 DNA + DeepSeek 综合 + Seedream 出 3 张候选)
  #4  Character Anchor    _agent_character_anchor         prompts/character_anchor_v2.md(立绘卡 Seedream)
  #5  Visual Assets       _agent_visual_assets_extractor  prompts/visual_assets_extractor.md
  #6  Director / 分镜     _agent_director                 prompts/director_v2.md(每格 200-400 字 prompt)
  #7  Image Generator     _agent_image_generator          Seedream 4.0
  #8  Visual QA           _agent_visual_qa                Qwen-VL Max + prompts/visual_verifier.md
  #9  Inpainter           _agent_inpainter                Doubao Seededit
  #10 Typesetter          _agent_typesetter               本地 PIL(对话气泡 / 旁白 / 拟声词)

漫创态 Sprint 5.B 战略性降级(2026-05-18)— 18-24 月观望,等 FLUX 2.0 / SD 4.0 / Wan 2.5 质变。
代码保留 + 入口 chip "⚗ 实验中",不投工程量;退出 credit 体系,改"订阅福利免费次数"。

【范本】对齐 simulation_service.py 风格(_RUNNING_*, kick_off, state 机)。

================================================================================
TODO(技术债 · 2026-06-02 上线后处理):
本文件已 3815 行,职责混杂(orchestrator + screenwriter + style director + panel
generator + typesetter + credit/refund + cron 等)。上线前夕拆分风险高(顺手 bug),
计划上线后 1-2 sprint 拆分为:
  - comic_orchestrator.py        ── 状态机 + kick_off + _RUNNING_COMICS(< 400 行)
  - comic_panel_generator.py     ── 并行生图 + 视觉资产 + retry(< 1500 行)
  - comic_typesetter_service.py  ── 排版 + 推送 + 缓存(< 800 行)
  - comic_credit_service.py      ── 计费 + 退款 + 配额(< 400 行)
本文件保留为 facade 重新 export 所有公共 API,确保零调用方修改.
设计:见 docs/refactor_comic_service.md(待写)。
================================================================================
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.db import execute, fetch_all, fetch_one
from app.models.comic import COMIC_STATES, TERMINAL_STATES, USER_DRIVEN_STATES, Comic
from app.services.llm_client import _openai_compat_call_json
from app.services.llm_routing import get_image_gen, get_vision_llm
from app.services.credit_service import (
    compute_comic_cancel_refund_units,
    consume_credits,
    credit_units_for_image_gen,
    credit_units_for_text_call,
    credit_units_for_vision_call,
    refund_credits,
)
from app.services.llm_routing.pricing import lookup_price


# Prompts 目录(对齐 simulation_service)
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# Sprint 5.1(2026-05-13)并行生图:页内 6 格用 ThreadPoolExecutor 并发跑。
# 跨页仍串行,保证 progress 推进 + db 写入清晰
#
# 默认值历史:
#   - Sprint 5.1 初始:4(火山方舟 ARK Seedream 5.0 付费版 QPS 5-10 安全裕度)
#   - 2026-05-13 用户切 SiliconFlow Kolors 免费,只支持 2 并发 → 临时降为 2
#   - 2026-05-14 用户切 SiliconFlow Qwen-Image,vendor 端并发不限,提到 **6**:
#     - 每页恰好 6 格,workers=6 → 整页一次性跑完,wall-clock = max(单图时间)
#     - Qwen-Image warm 后单图 ~15-20s → 每页 ~15-20s × 12 页 = **3-4 分钟**(原 2 并发 ~9-12 分钟)
#     - DeepSeek director LLM 并发上限通常 ≥ 6 QPS,_openai_compat_call_text 内置 retries=2 兜底
#
# 切回 2 / 4 的条件:vendor 报 429 限流频繁;改 env HUIMENG_PANEL_PARALLEL_WORKERS
# 时记得同步此默认值(没 env override 机制,直接改常量)
PANEL_PARALLEL_WORKERS = 6


def _load_prompt(name: str) -> str:
    """读 prompts/ 下的 markdown 模板。"""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# Sprint 2.B+ 六修(2026-05-12):本地上传参考图 → base64 data URL
# 给 Qwen-VL / Seedream 等外部 vision API 用 — 它们的服务器无法访问浑晶内部
# /api/comic-files/ 路径,必须转 data URL 内联传递。

_EXT_TO_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp",
}


def _resolve_image_url_for_vision(url: str) -> str:
    """把内部 /api/comic-files/ URL 转 base64 data URL,公网 URL 原样返。

    Args:
        url: 可能是 `/api/comic-files/{user_id}/{filename}` 或 `https://...`

    Returns:
        - 公网 URL:原样返(Qwen-VL 直接拉)
        - 内部 URL:`data:{mime};base64,{...}`(内联传递)

    Raises:
        ValueError:URL 格式非法 / 文件不存在 / 扩展名不支持
    """
    import base64

    if not url.startswith("/api/comic-files/"):
        return url   # 公网 URL(http/https/data:),原样返

    # 解析 /api/comic-files/{user_id}/{filename}
    rel = url[len("/api/comic-files/"):]
    parts = rel.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"非法 comic-files URL 格式:{url}")
    user_id, filename = parts

    file_path = settings.uploads_abs_dir.parent / "comic_refs" / user_id / filename
    if not file_path.exists():
        raise ValueError(f"参考图文件不存在:{file_path}")

    ext = file_path.suffix.lower()
    mime = _EXT_TO_MIME.get(ext)
    if not mime:
        raise ValueError(f"不支持的图片扩展名:{ext}")

    b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ============================================================
# 异常类
# ============================================================

class ComicNotFoundOrForbidden(Exception):
    """漫画 ID 不存在 / 不属于当前用户。"""


class ComicCancelled(Exception):
    """运行中检测到用户取消标志。"""


class ComicNotResumable(Exception):
    """漫画不能恢复(终态 / 没进度 / 等)。"""


class ComicStillRunning(Exception):
    """漫画当前正被 worker 跑,操作必须等其结束。"""


class InvalidComicSource(Exception):
    """source_json 不合法(缺 type / simulation_ids 为空 / upload_ids 不存在 / 等)。"""


class ComicPlanRequired(Exception):
    """2026-06-02:漫创态仅限 Pro 及以上会员(产品决策).

    Free 档用户尝试创建漫画时抛 — router 转 403 + error_code COMIC_PLAN_REQUIRED.
    前端拦截后弹升级 modal.
    """


# ============================================================
# 并发保护 — 同一漫画不能多 worker 同跑(double kick_off / resume 重复)
# ============================================================

_RUNNING_COMICS: dict[str, threading.Thread] = {}
_RUNNING_LOCK = threading.Lock()


def _register_comic(comic_id: str, thread: threading.Thread) -> None:
    with _RUNNING_LOCK:
        _RUNNING_COMICS[comic_id] = thread


def _unregister_comic(comic_id: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING_COMICS.pop(comic_id, None)


def is_comic_alive(comic_id: str) -> bool:
    """zombie 检测 — state 是 worker 推进态但 thread 不在注册表 → 僵尸。

    对齐 canonical_guardian_service.is_audit_alive / extract_service.is_extract_alive 模式。
    """
    with _RUNNING_LOCK:
        thread = _RUNNING_COMICS.get(comic_id)
        return thread is not None and thread.is_alive()


# ============================================================
# Helpers
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_comic_id() -> str:
    return uuid.uuid4().hex


def _generate_seed() -> int:
    """L4 一致性:整本漫画用同一 seed。
    范围:[1, 2^31 - 1](避开 0,某些 vendor 把 0 视为 random)。
    """
    return secrets.randbelow(2_147_483_646) + 1


# ============================================================
# 输入源校验
# ============================================================

def _validate_source(conn: sqlite3.Connection, user_id: str, source: dict) -> None:
    """校验 source_json 内容合法。

    source 形态:
      {"type": "internal", "simulation_ids": ["sim_uuid_1", ...]}
      {"type": "external", "upload_ids": ["upload_uuid_1", ...]}

    校验:
      - type 字段必填,值合法
      - internal:simulation_ids 非空 + 全部 user_id 匹配 + 全部 state='done'
      - external:upload_ids 非空 + 全部 user_id 匹配 + 全部 state='ready'
    """
    if not isinstance(source, dict):
        raise InvalidComicSource("source 必须是 JSON 对象")

    src_type = source.get("type")
    if src_type not in ("internal", "external"):
        raise InvalidComicSource(f"source.type 非法,期望 'internal' / 'external',收到 {src_type!r}")

    if src_type == "internal":
        sim_ids = source.get("simulation_ids") or []
        if not isinstance(sim_ids, list) or not sim_ids:
            raise InvalidComicSource("internal 源 simulation_ids 必填且非空")
        # 检查所有 sim 都属于 user 且 state='done'
        placeholders = ",".join(["?"] * len(sim_ids))
        rows = fetch_all(
            conn,
            f"SELECT id, user_id, state, project_id FROM simulations"
            f" WHERE id IN ({placeholders})",
            tuple(sim_ids),
        )
        if len(rows) != len(sim_ids):
            raise InvalidComicSource("某些 simulation_ids 不存在")
        for row in rows:
            if row["user_id"] != user_id:
                raise InvalidComicSource(f"simulation {row['id']} 不属于当前用户")
            if row["state"] != "done":
                raise InvalidComicSource(
                    f"simulation {row['id']} state={row['state']},必须为 done"
                )
        # UI 优化(2026-05-21 八轮)— 同项目内多选,跨项目禁止(防 API 直调绕过前端)
        project_ids = {row["project_id"] for row in rows}
        if len(project_ids) > 1:
            raise InvalidComicSource(
                "跨项目无法混拼漫画 — 所选 simulation 必须来自同一个项目"
            )
    else:   # external
        upload_ids = source.get("upload_ids") or []
        if not isinstance(upload_ids, list) or not upload_ids:
            raise InvalidComicSource("external 源 upload_ids 必填且非空")
        placeholders = ",".join(["?"] * len(upload_ids))
        rows = fetch_all(
            conn,
            f"SELECT id, user_id, state, project_id FROM uploads"
            f" WHERE id IN ({placeholders})",
            tuple(upload_ids),
        )
        if len(rows) != len(upload_ids):
            raise InvalidComicSource("某些 upload_ids 不存在")
        for row in rows:
            if row["user_id"] != user_id:
                raise InvalidComicSource(f"upload {row['id']} 不属于当前用户")
            if row["state"] != "ready":
                raise InvalidComicSource(
                    f"upload {row['id']} state={row['state']},必须为 ready"
                )
        # UI 优化(2026-05-21 八轮)— 同项目内多选,跨项目禁止(防 API 直调绕过前端)
        project_ids = {row["project_id"] for row in rows}
        if len(project_ids) > 1:
            raise InvalidComicSource(
                "跨项目无法混拼漫画 — 所选 upload 必须来自同一个项目"
            )


# ============================================================
# CRUD
# ============================================================

def create_comic(
    conn: sqlite3.Connection,
    user_id: str,
    name: str,
    source: dict,
    target_pages: int = 12,
) -> Comic:
    """创建漫画态项目。

    Sprint 1:落 db state='queued',不立即 kick_off worker
              (用户后续上传参考图 / 5 选 1 等用户驱动操作再推进)。

    Sprint C.4(2026-05-13):加 target_pages 参数 — 由 AI Planner 推荐 + 用户调整后传入。
    范围 6-18 页(对齐 prompts/screenwriter.md 铁律 1)。
    """
    if not name or not name.strip():
        raise ValueError("漫画项目 name 不能为空")
    if len(name.strip()) > 30:
        raise ValueError("漫画项目 name 最多 30 字")
    if not (6 <= target_pages <= 18):
        # Sprint 3 Phase 2(2026-05-13)接通 _agent_scripter 真分批承接后,
        # 上限从 Sprint C.4 临时回退的 12 重新放开到 18(对齐 schemas/comic.py)
        raise ValueError(f"target_pages 必须在 6-18 之间,实际:{target_pages}")

    # 2026-06-02 产品决策:漫创态仅限 Pro 及以上(不让 Free 白嫖,激发订阅意愿)
    # 防绕过前端直接 POST 创建
    user_row = fetch_one(conn, "SELECT plan FROM users WHERE id=?", (user_id,))
    if user_row:
        user_plan = user_row["plan"] or "free"
        if user_plan == "free":
            raise ComicPlanRequired(
                "漫创态是会员专属功能,升级 Pro 即可解锁"
            )

    _validate_source(conn, user_id, source)

    comic_id = _new_comic_id()
    now = _now_iso()
    seed = _generate_seed()

    execute(
        conn,
        """INSERT INTO comic_projects (
            id, user_id, name, source_json,
            style_tag, style_anchor_image_url, style_candidates_json,
            style_reference_image_urls_json, style_visual_dna_json,
            style_detailed_prompt,
            generation_seed,
            state, progress_percent,
            cost_yuan, error_message,
            target_pages,
            created_at, updated_at, completed_at
        ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, 0, NULL, ?, ?, ?, NULL)""",
        (
            comic_id, user_id, name.strip(), json.dumps(source, ensure_ascii=False),
            seed,
            "queued", 0,
            target_pages,
            now, now,
        ),
    )

    # ECON-2(2026-05-27 末⁴⁴):创建漫画时扣 1 个漫画包(如有);无包则跳过(走 founder 路径).
    # 该函数自身不 commit,与本 create_comic 同事务,失败时一并回滚.
    from app.services.credit_service import consume_one_comic_pack_if_available
    consume_one_comic_pack_if_available(conn, user_id, comic_id)

    conn.commit()

    return get_comic_or_404(conn, comic_id, user_id)


def get_comic_or_404(
    conn: sqlite3.Connection,
    comic_id: str,
    user_id: str,
) -> Comic:
    """按 id + user_id 拉漫画,404 / 403 都抛 ComicNotFoundOrForbidden。"""
    row = fetch_one(
        conn,
        "SELECT * FROM comic_projects WHERE id=? AND user_id=?",
        (comic_id, user_id),
    )
    if row is None:
        raise ComicNotFoundOrForbidden(f"comic {comic_id} 不存在 / 不属于当前用户")
    return Comic.from_row(row)


def list_comics_for_user(
    conn: sqlite3.Connection,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Comic]:
    """该用户的所有漫画项目(按 updated_at 倒序)。"""
    rows = fetch_all(
        conn,
        "SELECT * FROM comic_projects WHERE user_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )
    return [Comic.from_row(r) for r in rows]


def delete_comic(
    conn: sqlite3.Connection,
    comic_id: str,
    user_id: str,
) -> None:
    """删除漫画项目(级联删 character_cards / character_visuals / scenes / props / comic_pages)。"""
    comic = get_comic_or_404(conn, comic_id, user_id)
    # 跑中的漫画不能删
    if is_comic_alive(comic.id):
        raise ComicStillRunning(f"comic {comic_id} 正在跑,先取消再删")
    execute(conn, "DELETE FROM comic_projects WHERE id=?", (comic_id,))
    conn.commit()


# ============================================================
# 状态机更新
# ============================================================

def _update_progress(conn: sqlite3.Connection, comic_id: str, progress: int) -> None:
    """Sprint 5.x bug fix(2026-05-13):仅更新 progress(不动 state),
    让 agent 内部能细化推进进度。

    背景:用户报"进度条一进 generating 直接 85%,前面阶段卡很久不动"
    根因:_update_state 要求 new_state,router 只在 step 之间设大区间(10/25/50/70/80),
          agent 内部跑 30-60s 但 progress 不变;用户以为网卡了。
    修法:agent 内部循环 / 子步骤完成时调本 helper,在区间内平滑推进。
    """
    execute(
        conn,
        "UPDATE comic_projects SET progress_percent=?, updated_at=? WHERE id=?",
        (max(0, min(100, int(progress))), _now_iso(), comic_id),
    )
    conn.commit()


def _update_state(
    conn: sqlite3.Connection,
    comic_id: str,
    new_state: str,
    progress: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """更新 state + progress + 时间戳 + (终态)completed_at。"""
    if new_state not in COMIC_STATES:
        raise ValueError(f"非法 state:{new_state}")

    now = _now_iso()
    sets = ["state=?", "updated_at=?"]
    args: list = [new_state, now]
    if progress is not None:
        sets.append("progress_percent=?")
        args.append(progress)
    if error_message is not None:
        sets.append("error_message=?")
        args.append(error_message)
    if new_state in TERMINAL_STATES:
        sets.append("completed_at=?")
        args.append(now)
    args.append(comic_id)

    execute(
        conn,
        f"UPDATE comic_projects SET {', '.join(sets)} WHERE id=?",
        tuple(args),
    )

    # ECON-2.1(2026-05-27 末⁴⁴⁻¹):漫画失败/取消时退还漫画包,与 state 改动同事务.
    # 完成(done)不退 — 消耗正常.
    if new_state in ("failed", "cancelled"):
        from app.services.credit_service import refund_comic_pack_for_comic
        refund_comic_pack_for_comic(conn, comic_id)

    conn.commit()


# ============================================================
# Worker 入口 — 推进可后台跑的态(非 USER_DRIVEN_STATES)
# ============================================================

def run_comic(comic_id: str) -> None:
    """漫画 worker 主入口 — 推进当前 state 可推进的下一步。

    Sprint 1:仅状态机骨架,不真跑 LLM。
    Sprint 2+:按 state 路由到对应 _agent_*。
    """
    from app.db import get_connection
    conn = get_connection()
    try:
        _run_comic_inner(conn, comic_id)
    except Exception as e:
        traceback.print_exc()
        try:
            _update_state(
                conn, comic_id, "failed",
                error_message=f"{type(e).__name__}: {str(e)[:300]}",
            )
        except Exception as _inner:  # noqa: BLE001
            # 2026-06-02:不再静默吞 — 写状态失败比原错误更严重(僵尸 comic),必须 log
            import logging
            logging.getLogger(__name__).error(
                f"comic worker {comic_id}: 写 failed 状态时再次失败:"
                f" {type(_inner).__name__}: {_inner}",
                exc_info=True,
            )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _run_comic_inner(conn: sqlite3.Connection, comic_id: str) -> None:
    """主状态机循环 — Sprint 3(2026-05-13)接通 designing → done 全链路。

    入口状态:designing(用户 vote_style 完后状态机停在这里)
    出口状态:done(全部 panels 出图完成)/ failed(致命错误)

    用户驱动状态(style_uploading / style_voting)由 router 端点直接触发对应 agent,
    本函数不处理 — 见 routers/comics.api_upload_references / api_vote_style。

    designing → done 流程:
      1. designing → generating(进入图像生成主循环)
      2. 遍历 script.pages × panels,每格:
           a. Agent #6 Director(DeepSeek)→ 200-400 字 prompt
           b. Agent #7 Image Generator(Seedream 4.0 + 同 seed)→ image_url
           c. 累积到 panels_json,每页跑完 INSERT 一行 comic_pages
      3. progress 实时更新(generating 阶段 85→95)
      4. generating → composing(Sprint 4 实现 typesetter;C.5 简化为直接 done)
      5. composing → done(Sprint 4 接 _agent_typesetter 给整页 composed_url)
    """
    row = fetch_one(
        conn,
        "SELECT * FROM comic_projects WHERE id=?",
        (comic_id,),
    )
    if row is None:
        raise ComicNotFoundOrForbidden(f"comic {comic_id} 不存在")
    comic = Comic.from_row(row)

    if comic.state == "designing":
        _generate_all_panels(conn, comic)
        return

    if comic.state == "generating":
        # 已 in-progress(zombie 后重启)— 继续生
        _generate_all_panels(conn, comic, resume=True)
        return

    if comic.state == "composing":
        # Sprint 4.C(2026-05-13):composing 阶段 zombie 重启 — 重跑 typesetter
        # _agent_typesetter 输出文件 + db UPDATE 都是幂等,可安全重跑
        _run_typesetter_all_pages(conn, comic)
        return

    if comic.state in TERMINAL_STATES:
        return   # 已终态,no-op

    if comic.state in USER_DRIVEN_STATES:
        return   # 等用户操作

    # 其他状态(scripting / extracting_visuals 等)由 router 端点同步触发,
    # 不应该走 kick_off 路径
    raise NotImplementedError(
        f"comic state={comic.state!r} 不应通过 run_comic 异步推进 — "
        f"用户驱动态由 router 端点触发,Sprint 3 仅接通 designing → done"
    )


def _process_single_panel(
    comic_id: str,
    panel_dict_with_page: dict,
) -> tuple[dict, dict]:
    """Sprint 5.1(2026-05-13)并行生图 worker — 单 panel 完整处理(director + image_gen)。

    设计:
      - 在 worker thread 内独立创建 conn(SQLite check_same_thread=True 限制),
        director 内部读 character_cards / scenes / props 都用 thread-local conn
      - 不写 db(主线程汇总所有 panel 后一次写 comic_pages)
      - 不抛 exception(失败的 panel image_url=None,主线程根据 stats 计数失败)

    返回:
      (panel_result, stats)
      - panel_result: 干净的 panel dict,直接进 panels_json(schema 严格对齐串行版)
      - stats: 主线程汇总用的统计 + 错误信息
        {director_input_tokens, director_output_tokens, image_ok, error}
    """
    from app.db import get_connection

    panel_index = int(panel_dict_with_page.get("panel_index", 0))
    page_idx = panel_dict_with_page.get("page_index", 0)

    conn = get_connection()
    try:
        # 重新拉 comic(thread-local;数据本身只读,无并发问题)
        row = fetch_one(
            conn,
            "SELECT * FROM comic_projects WHERE id=?",
            (comic_id,),
        )
        if row is None:
            return (
                {
                    "panel_index": panel_index,
                    "image_url": None,
                    "prompt_used": None,
                    "dialogues": panel_dict_with_page.get("dialogues") or [],
                    "narrator": panel_dict_with_page.get("narrator"),
                    "sfx": panel_dict_with_page.get("sfx") or [],
                    "regenerated_count": 0,
                    "error": f"comic {comic_id} 不存在(worker 拉取时)",
                },
                {
                    "director_input_tokens": 0,
                    "director_output_tokens": 0,
                    "image_ok": False,
                    "error": "comic_not_found",
                },
            )
        comic_local = Comic.from_row(row)

        # Stage A:Director
        try:
            prompt_text, director_usage = _agent_director(
                comic_local, conn, panel_dict_with_page,
            )
            dir_in = director_usage.get("input_tokens", 0)
            dir_out = director_usage.get("output_tokens", 0)
        except Exception as e:  # noqa: BLE001
            err_msg = f"director: {type(e).__name__}: {str(e)[:200]}"
            return (
                {
                    "panel_index": panel_index,
                    "image_url": None,
                    "prompt_used": None,
                    "dialogues": panel_dict_with_page.get("dialogues") or [],
                    "narrator": panel_dict_with_page.get("narrator"),
                    "sfx": panel_dict_with_page.get("sfx") or [],
                    "regenerated_count": 0,
                    "error": err_msg,
                },
                {
                    "director_input_tokens": 0,
                    "director_output_tokens": 0,
                    "image_ok": False,
                    "error": f"page {page_idx} panel {panel_index} {err_msg}",
                },
            )

        # Stage B:Image Generator(Sprint 4.D+:1:1 与 reader grid 对齐)
        image_url, img_usage = _agent_image_generator(
            comic_local, prompt_text, aspect_ratio="1:1",
        )
        image_ok = bool(image_url)

        # Stage C(Sprint 5.11 Reflexion,2026-05-14):Qwen-VL Max 视觉校验 +
        # 推荐组合 1A+2B+3A — 重生上限 1 次;只 speaker_missing fail 触发;
        # verifier_result 落 panel JSON(零 schema migration)
        verifier_result: dict = {}
        regen_image_usage: dict = {}
        regenerated = False
        if image_ok:
            try:
                verifier_result = _agent_visual_verifier(
                    comic_local, panel_dict_with_page, image_url, conn,
                )
            except Exception as e:  # noqa: BLE001
                # verifier 自身永不应抛(内部全 try/except),这里是最后兜底
                verifier_result = {
                    "verdict": "skip",
                    "fail_reasons": [],
                    "skip_reason": f"verifier_exception: {type(e).__name__}: {str(e)[:120]}",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }

            # 条件重生(只 speaker_missing,最多 1 次)
            if verifier_result.get("verdict") == "fail":
                try:
                    new_url, regen_image_usage, new_prompt = (
                        _maybe_regenerate_panel_for_speaker_missing(
                            comic_local,
                            panel_dict_with_page,
                            prompt_text,
                            verifier_result,
                            conn,
                        )
                    )
                    if new_url:
                        # 重生成功 — 覆盖 image_url + prompt_used
                        image_url = new_url
                        prompt_text = new_prompt or prompt_text
                        regenerated = True
                except Exception:  # noqa: BLE001
                    # 重生本身失败保留原图,verifier_result 仍记 verdict=fail 留 audit
                    pass

        panel_result = {
            "panel_index": panel_index,
            "image_url": image_url,
            "prompt_used": prompt_text,
            "dialogues": panel_dict_with_page.get("dialogues") or [],
            "narrator": panel_dict_with_page.get("narrator"),
            "sfx": panel_dict_with_page.get("sfx") or [],
            "regenerated_count": 1 if regenerated else 0,
            # Sprint 5.11(2026-05-14):verifier_result 落 panel JSON,
            # 给后续 audit / reader UI 标"AI 自检不达标"提示用
            "verifier_result": verifier_result or None,
        }
        verifier_usage = verifier_result.get("usage") or {} if isinstance(verifier_result, dict) else {}
        stats = {
            "director_input_tokens": dir_in,
            "director_output_tokens": dir_out,
            "image_ok": bool(image_url),
            "regenerated": regenerated,
            # Sprint 5.11:vision verifier token 单独统计,供 credit 扣费汇总
            "verifier_input_tokens": int(verifier_usage.get("input_tokens", 0) or 0),
            "verifier_output_tokens": int(verifier_usage.get("output_tokens", 0) or 0),
            "error": (
                None if image_ok else
                f"page {page_idx} panel {panel_index} image_gen failed: "
                f"{img_usage.get('error', 'unknown')}"
            ),
        }
        return panel_result, stats
    finally:
        conn.close()


def _generate_all_panels(
    conn: sqlite3.Connection,
    comic: Comic,
    resume: bool = False,
) -> None:
    """漫画主循环:遍历 script.pages × panels → director + image_gen → 写 comic_pages。

    Sprint 3 设计(2026-05-13)+ Sprint 5.1 并行加速(2026-05-13):
      - 页内并行:每页 6 格用 ThreadPoolExecutor(PANEL_PARALLEL_WORKERS) 并发跑
        (每 panel 独立 conn,无并发竞争;Seedream / DeepSeek 都是 stateless 调用,
         结果数学上等同串行)
      - 跨页串行:保证 progress 推进 + db 写入顺序 + 进度条体验
      - 失败的格 image_url=None 但流程继续(不阻塞整本)
      - progress 实时:85 → 95(generating 阶段),每页更新一次
      - resume:扫已存在 comic_pages 跳过已完成 page(zombie 重启友好)
      - credit 累计:跑完所有 panels 后 consume_credits(action=comic_director / comic_image_gen)

    完成后:state → composing → _run_typesetter_all_pages → done

    并发安全:_process_single_panel worker 内部独立 conn;主线程只在 panel 全完成后
    汇总 stats + 写 db,无 race condition。
    """
    if not comic.script or "pages" not in comic.script:
        raise ValueError(f"comic {comic.id} 无 script,无法生成")
    pages: list[dict] = comic.script.get("pages") or []
    if not pages:
        raise ValueError(f"comic {comic.id} script.pages 为空")

    # 拉 resume 时已完成的 page_index 集合
    existing_page_indexes: set[int] = set()
    if resume:
        existing_rows = fetch_all(
            conn,
            "SELECT page_index FROM comic_pages WHERE comic_id=? AND state='generating'",
            (comic.id,),
        )
        existing_page_indexes = {int(r["page_index"]) for r in existing_rows}

    _update_state(conn, comic.id, "generating", progress=86)

    total_pages = len(pages)
    total_director_in = 0
    total_director_out = 0
    total_images_ok = 0
    total_images_fail = 0
    # Sprint 5.11 Reflexion(2026-05-14):reflexion-related 计费汇总
    total_regenerated = 0    # 触发 speaker-grounding 重生的 panel 数(每次额外 +1 image_gen)
    total_verifier_in = 0    # Qwen-VL Max 校验消耗 input token 汇总
    total_verifier_out = 0   # Qwen-VL Max 校验消耗 output token 汇总
    panel_errors: list[str] = []

    for page_idx, page in enumerate(pages, 1):
        if page_idx in existing_page_indexes:
            continue   # resume 时跳过

        panels: list[dict] = page.get("panels") or []
        if not panels:
            continue

        # === Sprint 5.1 页内并发:ThreadPoolExecutor 跑 N 个 panel ===
        # 每 task 独立 conn,task 间无竞争;主线程只等结果汇总
        result_panels: list[dict] = []
        # 准备 task 输入(page_index 注入每 panel)
        panel_inputs: list[dict] = []
        for panel_dict in panels:
            pdw = dict(panel_dict)
            pdw["page_index"] = page_idx
            # 兜底 panel_index(LLM 有时遗漏)
            if "panel_index" not in pdw:
                pdw["panel_index"] = len(panel_inputs) + 1
            panel_inputs.append(pdw)

        # 启动并发 workers
        with ThreadPoolExecutor(
            max_workers=PANEL_PARALLEL_WORKERS,
            thread_name_prefix=f"panel-p{page_idx}",
        ) as executor:
            future_to_input = {
                executor.submit(_process_single_panel, comic.id, pdw): pdw
                for pdw in panel_inputs
            }
            for future in as_completed(future_to_input):
                try:
                    # 2026-06-02 hotfix R6/Y4:加 5min 单 panel 超时
                    # 防单个 panel LLM 卡住,整页死等(原行为)
                    panel_result, stats = future.result(timeout=300)
                except Exception as e:  # noqa: BLE001
                    # _process_single_panel 内部不应抛(全部 try/except),
                    # 万一抛出(含 timeout)→ 兜底
                    pdw = future_to_input[future]
                    panel_idx_fallback = pdw.get("panel_index", 0)
                    err_msg = f"worker unexpectedly raised: {type(e).__name__}: {e}"
                    panel_result = {
                        "panel_index": panel_idx_fallback,
                        "image_url": None,
                        "prompt_used": None,
                        "dialogues": pdw.get("dialogues") or [],
                        "narrator": pdw.get("narrator"),
                        "sfx": pdw.get("sfx") or [],
                        "regenerated_count": 0,
                        "error": err_msg,
                    }
                    stats = {
                        "director_input_tokens": 0,
                        "director_output_tokens": 0,
                        "image_ok": False,
                        "error": f"page {page_idx} panel {panel_idx_fallback} {err_msg}",
                    }
                # 汇总
                result_panels.append(panel_result)
                total_director_in += stats["director_input_tokens"]
                total_director_out += stats["director_output_tokens"]
                if stats["image_ok"]:
                    total_images_ok += 1
                else:
                    total_images_fail += 1
                    if stats.get("error"):
                        panel_errors.append(stats["error"])
                # Sprint 5.11 Reflexion 计费汇总(stats 是新字段,旧 worker 路径
                # 不存在时 .get 兜底 0,向后兼容)
                if stats.get("regenerated"):
                    total_regenerated += 1
                total_verifier_in += int(stats.get("verifier_input_tokens", 0) or 0)
                total_verifier_out += int(stats.get("verifier_output_tokens", 0) or 0)

        # 并发完成顺序 ≠ panel_index 顺序;**按 panel_index 升序排重建 page 顺序**
        result_panels.sort(key=lambda p: int(p.get("panel_index", 0)))

        # 写 comic_pages 行(每页一次,主线程串行写)
        now = _now_iso()
        page_id = uuid.uuid4().hex
        try:
            execute(
                conn,
                """INSERT INTO comic_pages
                   (id, comic_id, page_index, panels_json, composed_url, state,
                    regenerated_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, 'generating', 0, ?, ?)
                   ON CONFLICT(comic_id, page_index) DO UPDATE SET
                     panels_json=excluded.panels_json,
                     state=excluded.state,
                     updated_at=excluded.updated_at""",
                (page_id, comic.id, page_idx,
                 json.dumps(result_panels, ensure_ascii=False),
                 now, now),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            panel_errors.append(f"page {page_idx} db write failed: {e}")

        # 实时进度:85 → 95(generating 阶段)
        progress_pct = 85 + int((page_idx / total_pages) * 10)
        _update_state(conn, comic.id, "generating", progress=min(95, progress_pct))

    # ===== Sprint 5.B(2026-05-18)漫画态降级:不再扣 credit =====
    # 漫画态从"按 credit 消耗"改为"订阅福利免费次数"(Free 0 / Pro 1 / Max 2 / 超级 Max 4 本/月),
    # 占用判定由 enforce_comic_count_quota 在创建入口完成(routers/comics.py:api_create_comic);
    # 本处仅 log 实际 vendor 消耗供运维 audit(平台成本核算),**不消耗用户钱包**。
    #
    # 原 5 个 consume_credits 调用(comic_director / comic_image_gen / comic_visual_verifier /
    # comic_style_dna / comic_style_synth / comic_style_candidate)全删 — 改成 logging 留痕。
    try:
        import logging
        billed_image_count = total_images_ok + total_regenerated
        platform_cost_yuan = 0.0
        if total_director_in or total_director_out:
            platform_cost_yuan += lookup_price(
                "deepseek", settings.llm_model, "token",
                total_director_in, total_director_out,
            )
        if billed_image_count > 0:
            platform_cost_yuan += lookup_price(
                "jimeng", settings.jimeng_model, "image",
                0, 0, image_count=billed_image_count,
            )
        if total_verifier_in or total_verifier_out:
            platform_cost_yuan += lookup_price(
                "qwen_vl", settings.qwen_vl_model, "token",
                total_verifier_in, total_verifier_out,
            )
        logging.info(
            f"[comic generation cost audit] comic={comic.id} user={comic.user_id} "
            f"director_tokens={total_director_in}/{total_director_out} "
            f"images_ok={total_images_ok} regenerated={total_regenerated} "
            f"verifier_tokens={total_verifier_in}/{total_verifier_out} "
            f"platform_cost_yuan={platform_cost_yuan:.4f} "
            f"(Sprint 5.B: 不计入用户 credit;次数池由 enforce_comic_count_quota 控制)"
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"[comic cost audit log] failed for {comic.id}: {e}")

    # ===== 终态判定 =====
    if total_images_ok == 0:
        # 全失败:state=failed
        err_msg = f"全部 {total_images_fail} 张图生成失败" + (
            ";详:" + "; ".join(panel_errors[:3]) if panel_errors else ""
        )
        _update_state(
            conn, comic.id, "failed", progress=95,
            error_message=err_msg[:300],
        )
        return

    # Sprint 4.C(2026-05-13):generating 跑完 → composing(PIL 排版)→ done
    # 历史:Sprint 3 简化期 generating → done 跳过 composing;Sprint 4.C 接通真排版
    _run_typesetter_all_pages(conn, comic)


def _run_typesetter_all_pages(conn: sqlite3.Connection, comic: Comic) -> None:
    """Sprint 4.C(2026-05-13):composing 阶段桥接 — 循环排版所有页 → done。

    流程:
      1. state → composing(progress 95);拉所有 comic_pages 按 page_index 升序
      2. 每页:_agent_typesetter(PIL 本地合成)→ UPDATE composed_url + state='composed'
      3. 失败页:log warning + composed_url 留 NULL + state='failed';继续下一页
         (soft fail 哲学,对齐 ADR Sprint 4.C 决策点 5)
      4. 完成 → state='done'(progress 100)

    幂等(zombie 重启友好):重跑同 comic_id 时,_agent_typesetter 会覆盖同路径 PNG;
    db UPDATE 也是覆写;无重复副作用。

    Credit:PIL 本地零成本,不调 consume_credits(consume_credits units=0 是 no-op,
    记 0c 行无审计价值;若未来切云端排版 API 再启用)。
    """
    pages_rows = fetch_all(
        conn,
        "SELECT id, page_index, panels_json FROM comic_pages "
        "WHERE comic_id=? ORDER BY page_index ASC",
        (comic.id,),
    )

    _update_state(conn, comic.id, "composing", progress=95)

    if not pages_rows:
        # 边缘:没有 comic_pages 行(应该不可能,_generate_all_panels 之前已写入)
        # 仍走到 done,前端 reader 会显"暂无生成页面"占位
        _update_state(conn, comic.id, "done", progress=100)
        return

    total = len(pages_rows)
    ok_count = 0
    fail_count = 0
    fail_errors: list[str] = []

    for idx, row in enumerate(pages_rows, start=1):
        page_index = int(row["page_index"])
        try:
            panels_json = json.loads(row["panels_json"]) if row["panels_json"] else []
            if not isinstance(panels_json, list):
                panels_json = []
        except Exception:  # noqa: BLE001
            panels_json = []

        try:
            composed_url = _agent_typesetter(comic, page_index, panels_json)
            execute(
                conn,
                "UPDATE comic_pages SET composed_url=?, state='composed', "
                "updated_at=? WHERE id=?",
                (composed_url, _now_iso(), row["id"]),
            )
            conn.commit()
            ok_count += 1
        except Exception as e:  # noqa: BLE001
            import logging
            logging.warning(
                f"typesetter failed page {page_index} of comic {comic.id}: "
                f"{type(e).__name__}: {e}"
            )
            fail_errors.append(f"p{page_index}: {type(e).__name__}: {str(e)[:120]}")
            fail_count += 1
            # composed_url 留 NULL → reader 自动降级到 panel grid 模式
            try:
                execute(
                    conn,
                    "UPDATE comic_pages SET state='failed', updated_at=? WHERE id=?",
                    (_now_iso(), row["id"]),
                )
                conn.commit()
            except Exception:  # noqa: BLE001
                pass

        # 进度推进:95 → 99(留 100 给最终 done)
        progress = 95 + int(idx / total * 4)
        _update_state(conn, comic.id, "composing", progress=min(99, progress))

    # 终态:即使部分页 typesetter 失败,整本仍 done(soft fail)
    # 完全失败(全部页失败)也 done — reader 会显 panel grid 兜底视图
    _update_state(conn, comic.id, "done", progress=100)


def kick_off(comic_id: str) -> None:
    """异步启动 worker(对齐 simulation_service 默认 kick_off 模式)。

    Sprint 1:仅注册 thread 框架,但 worker 内部抛 NotImplementedError;
              用于验证 _RUNNING_COMICS 注册 + zombie 检测正确。
    """
    def _run_with_register() -> None:
        try:
            run_comic(comic_id)
        finally:
            _unregister_comic(comic_id)

    # 2026-06-05 BYOK:capture context 把 endpoint 的 user_id 带进 thread
    from app.services.byok_context import capture_current_context
    ctx = capture_current_context()
    t = threading.Thread(
        target=ctx.run,
        args=(_run_with_register,),
        daemon=True,
        name=f"comic-{comic_id[:8]}",
    )
    _register_comic(comic_id, t)
    t.start()


def cancel_comic(
    conn: sqlite3.Connection,
    comic_id: str,
    user_id: str,
) -> tuple[int, str]:
    """用户主动取消运行中的漫画 — Sprint C.1 credit 重构。

    退款规则(对齐 credit_service.compute_comic_cancel_refund_units):
      progress <  10:全退(已扣 credit 全额退回 subscription wallet)
      progress < 80:半退
      progress >= 80:不退

    Sprint C.1 注:本函数依赖 comic._consumed_credits_total 字段计算总消耗,
    但当前(C.1 Step 2)credit 真扣还未接通(在 Sprint C.3 接通);
    所以 Sprint C.1 内 refund_units 当作"占位 placeholder"返回,
    Sprint C.3 接通各 _agent_* 内的 consume_credits 后,SUM(credit_transactions)
    定能算出真实退款值。

    返回 (refund_units, refund_phase):
      refund_phase:"full" / "half" / "none" / "noop"
    """
    comic = get_comic_or_404(conn, comic_id, user_id)
    if comic.state in TERMINAL_STATES:
        return (0, "noop")

    # Sprint C.2:算本次漫画已真实消耗多少 credit
    #   SUM(credit_transactions.delta) WHERE related_id=comic_id AND kind='consume'
    #   delta 是负数(消耗),取 abs 得总扣减
    consumed_row = fetch_one(
        conn,
        """SELECT COALESCE(SUM(delta), 0) AS total_delta FROM credit_transactions
           WHERE related_id=? AND kind='consume'""",
        (comic.id,),
    )
    total_consumed = abs(int(consumed_row["total_delta"])) if consumed_row else 0

    # 按 progress 算退款(0-10% 全退 / 10-80% 半退 / 80%+ 不退)
    refund_units, refund_phase = compute_comic_cancel_refund_units(
        comic.progress_percent, total_consumed
    )

    # 3. 真退 credit 到 subscription wallet
    if refund_units > 0:
        try:
            refund_credits(
                conn,
                user_id=user_id,
                action=f"comic_cancel_refund_{refund_phase}",
                units=refund_units,
                related_id=comic.id,
                metadata={
                    "progress_at_cancel": comic.progress_percent,
                    "refund_phase":       refund_phase,
                },
            )
        except Exception as e:
            import logging
            # 2026-06-02 hotfix Y2:exc_info=True 保堆栈,治"退款失败后排查无线索"
            logging.warning(
                f"[comic.cancel] refund_credits failed: {e}",
                exc_info=True,
            )

    # 4. 状态机推 cancelled
    refund_label = "全额" if refund_phase == "full" else ("半额" if refund_phase == "half" else "未退还")
    _update_state(
        conn, comic.id, "cancelled",
        error_message=f"用户主动取消(进度 {comic.progress_percent}%,配额{refund_label}退回)",
    )

    return (refund_units, refund_phase)


# ============================================================
# Source 文本 / DB 落库 helpers(Sprint 2.A 新)
# ============================================================

def _load_source_text_from_dict(conn: sqlite3.Connection, source: dict) -> str:
    """从 source dict 直接拉文本 — Sprint C.4 为 plan_preview 提供的入口
    (planner 在创建 comic 前需要扫源文本估页数,所以接 dict 而非 Comic 对象)。"""
    if source.get("type") == "internal":
        sim_ids = source.get("simulation_ids") or []
        if not sim_ids:
            return ""
        ph = ",".join(["?"] * len(sim_ids))
        rows = fetch_all(
            conn,
            f"SELECT id, narrative FROM simulations WHERE id IN ({ph}) ORDER BY created_at",
            tuple(sim_ids),
        )
        parts = [(r["narrative"] or "") for r in rows]
        return "\n\n--- 章节切 ---\n\n".join(p for p in parts if p)
    elif source.get("type") == "external":
        upload_ids = source.get("upload_ids") or []
        if not upload_ids:
            return ""
        ph = ",".join(["?"] * len(upload_ids))
        # Sprint 4.D+ bug fix(2026-05-13)双 bug 修复:
        # bug A(用户实测 500 根因): 原 `ORDER BY created_at` 错列名(uploads 只有 uploaded_at)
        # bug B(更深 Sprint 2.A 潜伏): 原代码读 `r["file_text"]`,但 uploads 表 schema
        #       (011_uploads.sql)从未有此列 → external 源整条路径从未真正工作过
        # 修法:从 storage_path 实时 parse(零 db schema 改,零 migration)
        #       开销可接受:plan_preview / scripter 各跑一次,平均文件 < 5MB
        rows = fetch_all(
            conn,
            f"SELECT id, storage_path, mime_type FROM uploads "
            f"WHERE id IN ({ph}) ORDER BY uploaded_at",
            tuple(upload_ids),
        )
        from app.services.file_parser import parse_file
        parts: list[str] = []
        for r in rows:
            try:
                file_path = settings.uploads_abs_dir / r["storage_path"]
                if not file_path.exists():
                    import logging
                    logging.warning(
                        f"upload {r['id']} storage 文件丢失:{file_path}"
                    )
                    continue
                parsed = parse_file(file_path, r["mime_type"])
                if parsed.success and parsed.text:
                    parts.append(parsed.text)
                elif parsed.error:
                    import logging
                    logging.warning(
                        f"重 parse upload {r['id']} 失败:{parsed.error}"
                    )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"重 parse upload {r['id']} 异常:{type(e).__name__}: {e}"
                )
        return "\n\n--- 章节切 ---\n\n".join(parts)
    return ""


def _load_source_text(conn: sqlite3.Connection, comic: Comic) -> str:
    """从 comic.source 拉拼接的全本文本(simulation narratives OR upload file_text)。

    Sprint C.4 起委托给 _load_source_text_from_dict(更通用)。
    """
    return _load_source_text_from_dict(conn, comic.source)




def _save_visual_assets(
    conn: sqlite3.Connection,
    comic_id: str,
    extracted: dict,
    character_id_by_name: dict[str, str],
) -> dict[str, int]:
    """Agent #5 产物落 db:character_visuals / scenes / props 三表。

    extracted 期望结构:
      {
        "character_visuals": [{name, face_json, hair_json, body_json, outfit_json,
                                accessories_json, signature_props_json, soul_traits}, ...],
        "scenes": [{name, location_type, era, architecture_style, lighting, season,
                    key_props_json}, ...],
        "props": [{name, prop_type, owner_character_name, visual_description,
                    story_significance}, ...]
      }

    character_id_by_name 用于把 character_visuals[i].name / props[i].owner_character_name
    解析到 character.id(不命中的角色跳过 — 防 LLM 抽出原文未存在的角色)。

    返回 {"visuals": N, "scenes": N, "props": N} 三表 insert 数。
    """
    now = _now_iso()
    counts = {"visuals": 0, "scenes": 0, "props": 0}

    # character_visuals
    for cv in extracted.get("character_visuals") or []:
        name = cv.get("name")
        char_id = character_id_by_name.get(name) if name else None
        if not char_id:
            continue   # 跳过原文未存在角色
        execute(
            conn,
            """INSERT INTO character_visuals (
                id, comic_id, character_id,
                face_json, hair_json, body_json, outfit_json,
                accessories_json, signature_props_json, soul_traits,
                focused_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(comic_id, character_id) DO UPDATE SET
                face_json=excluded.face_json,
                hair_json=excluded.hair_json,
                body_json=excluded.body_json,
                outfit_json=excluded.outfit_json,
                accessories_json=excluded.accessories_json,
                signature_props_json=excluded.signature_props_json,
                soul_traits=excluded.soul_traits,
                updated_at=excluded.updated_at""",
            (
                uuid.uuid4().hex, comic_id, char_id,
                json.dumps(cv.get("face_json") or {}, ensure_ascii=False),
                json.dumps(cv.get("hair_json") or {}, ensure_ascii=False),
                json.dumps(cv.get("body_json") or {}, ensure_ascii=False),
                json.dumps(cv.get("outfit_json") or {}, ensure_ascii=False),
                json.dumps(cv.get("accessories_json") or [], ensure_ascii=False),
                json.dumps(cv.get("signature_props_json") or [], ensure_ascii=False),
                cv.get("soul_traits"),
                now, now,
            ),
        )
        counts["visuals"] += 1

    # scenes
    for sc in extracted.get("scenes") or []:
        name = sc.get("name")
        if not name:
            continue
        execute(
            conn,
            """INSERT INTO scenes (
                id, comic_id, name, location_type, era, architecture_style,
                lighting, season, key_props_json,
                focused_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                uuid.uuid4().hex, comic_id, name,
                sc.get("location_type"), sc.get("era"),
                sc.get("architecture_style"), sc.get("lighting"), sc.get("season"),
                json.dumps(sc.get("key_props_json") or [], ensure_ascii=False),
                now, now,
            ),
        )
        counts["scenes"] += 1

    # props
    for pr in extracted.get("props") or []:
        name = pr.get("name")
        if not name:
            continue
        owner_name = pr.get("owner_character_name")
        owner_id = character_id_by_name.get(owner_name) if owner_name else None
        execute(
            conn,
            """INSERT INTO props (
                id, comic_id, name, prop_type, owner_character_id,
                visual_description, story_significance,
                focused_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                uuid.uuid4().hex, comic_id, name,
                pr.get("prop_type"), owner_id,
                pr.get("visual_description"), pr.get("story_significance"),
                now, now,
            ),
        )
        counts["props"] += 1

    conn.commit()
    return counts


def _strip_markdown_fence(s: str) -> str:
    """剥 ```json ... ``` 包裹(对齐 llm_client._strip_markdown_fence)。"""
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _load_character_id_map(conn: sqlite3.Connection, comic: Comic) -> dict[str, str]:
    """从 comic.source 拉本作品的 character.name → character.id 映射。

    - internal:从 simulations.characters_snapshot 拉(已冻结的快照,不受角色后续删改影响)
    - external:upload 模式无 characters 表关联,返回空 dict(暂不支持外部文本独立 character)
                Sprint 3 计划:Agent #2 编剧 在外部文本模式时自动抽取主角入临时 characters 表
    """
    src = comic.source
    name_to_id: dict[str, str] = {}
    if src.get("type") == "internal":
        sim_ids = src.get("simulation_ids") or []
        if not sim_ids:
            return {}
        ph = ",".join(["?"] * len(sim_ids))
        rows = fetch_all(
            conn,
            f"SELECT characters_snapshot FROM simulations WHERE id IN ({ph})",
            tuple(sim_ids),
        )
        for r in rows:
            snapshot_raw = r["characters_snapshot"]
            if not snapshot_raw:
                continue
            try:
                snapshot = json.loads(snapshot_raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(snapshot, list):
                continue
            for c in snapshot:
                if isinstance(c, dict) and c.get("name") and c.get("id"):
                    name_to_id[c["name"]] = c["id"]
    return name_to_id


# ============================================================
# Agent 函数 — Sprint 2.A 起 4 个 agent 真实实现;5-10 仍 stub
# ============================================================
# 对齐 simulation_service 的 _build_*_prompt + run_simulation 内联调用 LLM 模式。

def _agent_planner(
    source_text: str,
    character_count: int,
    major_event_count: int = 0,
    user_preference: str = "auto",
) -> dict:
    """Agent #1.5 AI Planner — 扫源文本 + 图谱 → 推荐目标页数 + credit 预算。
       Sprint C.4(2026-05-13)。

    输入(全 str / int,不依赖 Comic 实体 — 创建前预览可调):
      source_text:        拼接好的源全文(_load_source_text 输出)
      character_count:    图谱角色数(用户没图谱时传 0)
      major_event_count:  关键事件数(用户没图谱时传 0)
      user_preference:    "auto" / "short" / "long"

    输出:planner.md 定义的 JSON,含 recommended_total_pages / estimated_credits / reasoning / long_text_warning

    Raises:
      ValueError:LLM 输出 JSON 无法解析,或字段缺失 / 范围超界
    """
    source_char_count = len(source_text)
    system_prompt = _load_prompt("planner.md")
    user_input = {
        "source_char_count": source_char_count,
        "character_count": character_count,
        "major_event_count": major_event_count,
        "user_preference": user_preference,
    }

    parsed, _usage = _openai_compat_call_json(
        system_prompt=system_prompt,
        user_input=user_input,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        vendor_label="DeepSeek-Planner",
        max_tokens=500,    # 输出短 JSON
        temperature=0.3,
        timeout=30.0,
    )

    # 校验
    if not isinstance(parsed, dict):
        raise ValueError(f"planner LLM 输出非 dict:{type(parsed).__name__}")
    pages = parsed.get("recommended_total_pages")
    if not isinstance(pages, int) or not (6 <= pages <= 18):
        # Sprint 3 Phase 2(2026-05-13)接通分批承接后放开到 6-18
        # 容错:LLM 偶尔会推 19+,本侧 clamp 到 18(< 6 仍报错)
        if isinstance(pages, int) and pages > 18:
            parsed["recommended_total_pages"] = 18
            pages = 18
        else:
            raise ValueError(
                f"planner 推荐页数 {pages!r} 非法,必须 6-18 整数"
            )
    estimated = parsed.get("estimated_credits")
    if not isinstance(estimated, int) or estimated < 100:
        raise ValueError(
            f"planner credit 预估 {estimated!r} 非法,必须 ≥ 100 整数"
        )
    if not parsed.get("reasoning"):
        raise ValueError("planner 输出缺 reasoning 字段")

    return parsed


def _scripter_batch(
    *,
    batch_start_page: int,
    batch_end_page: int,
    total_pages: int,
    is_first_batch: bool,
    is_last_batch: bool,
    previous_tail_context: str,
    source_text_with_note: str,
    char_names_text: str,
    name_to_id: dict,
) -> tuple[dict, dict]:
    """单批编剧 LLM 调用 — Sprint 3 Phase 2 真分批承接的"原子操作"。

    输入约束:每批 batch_end_page − batch_start_page + 1 ≤ 6 页;每页 6 格。
    输出约束:本批 ≤ 36 格 panels 的 JSON,~10-12K 字符(约 4-5K token),
              远低于 DeepSeek V3 ~8K output token 硬上限。

    历史教训(Sprint 2.B+ → C.4 → 3 Phase 2):
      - 单次出 30 页 / 18 页 / 12 页 全部撞 8K token JSON 截断。
      - Sprint 3 Phase 2 改"6 页/批 × N 批"才真正解决。
      - 见 项目记忆.md 教训 #26。

    返回 (batch_dict, usage_dict)。batch_dict 含 title / pages / tail_context;
    usage_dict 含 input_tokens / output_tokens(供上层汇总扣 credit)。
    """
    system_prompt = _load_prompt("screenwriter.md")
    user_input = {
        "batch_start_page": batch_start_page,
        "batch_end_page": batch_end_page,
        "total_pages": total_pages,
        "panels_per_page": 6,
        "is_first_batch": is_first_batch,
        "is_last_batch": is_last_batch,
        "previous_tail_context": previous_tail_context,
        "source_summary": f"作品涉及角色:{char_names_text}",
        "graph_summary": f"角色 {len(name_to_id)} 个" if name_to_id else "(无图谱)",
        "source_text": source_text_with_note,
    }

    parsed, usage = _openai_compat_call_json(
        system_prompt=system_prompt,
        user_input=user_input,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        vendor_label=f"DeepSeek-Scripter(batch p{batch_start_page}-{batch_end_page})",
        max_tokens=8000,
        temperature=0.6,
        timeout=120.0,
    )

    # 形态校验
    if not isinstance(parsed, dict) or "pages" not in parsed:
        raise ValueError(
            f"编剧 LLM 输出形态错误(batch p{batch_start_page}-{batch_end_page}):"
            f"期望含 'pages' 字段的 dict,"
            f"实际 keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        )
    if not isinstance(parsed["pages"], list) or len(parsed["pages"]) == 0:
        raise ValueError(
            f"编剧 LLM 输出 pages 为空(batch p{batch_start_page}-{batch_end_page})"
        )

    # 页号校验 + 容错:首页 page_index 必须 = batch_start_page
    expected_pages = batch_end_page - batch_start_page + 1
    first_page_index = parsed["pages"][0].get("page_index")
    if first_page_index != batch_start_page:
        # 容错:LLM 偶尔从 1 开始重新编号,本侧强制 renumber 对齐 batch_start_page
        for i, page in enumerate(parsed["pages"]):
            page["page_index"] = batch_start_page + i

    # 截断多产(LLM 偶尔多产一页,orchestrator 拼装时会让总页数超目标)
    if len(parsed["pages"]) > expected_pages:
        parsed["pages"] = parsed["pages"][:expected_pages]

    # tail_context 缺失兜底(空字符串,下一批就不知道接哪;不致命,仅日志)
    if not isinstance(parsed.get("tail_context"), str):
        parsed["tail_context"] = ""

    return parsed, usage


# ============================================================
# Sprint 5.10(2026-05-14)工程层兜底:Shot Type 多样性强制
# ============================================================
#
# 背景:Sprint 5.7 screenwriter v3 写了 12 铁律(每页 ≥ 4 种 shot_type / 首格必 wide /
#       群像必 group),但 LLM 大量违反 — 实测产物仍全大头近景。
# 教训 #31:**prompt 层规则 ≠ 工程层强制**,LLM 永远会违反规则,后端必须程序兜底
# 修法:scripter 输出后程序级强制改 shot_type,不靠 LLM 自律

_WIDE_SHOT_TYPES = {"远景", "全景", "群像"}    # 首格必属此集合
_GROUP_SHOT_TYPES = {"群像", "过肩"}            # 群像场景必有 ≥ 1 格属此集合


def _enforce_shot_diversity(pages: list[dict]) -> dict:
    """Sprint 5.10:scripter 输出后强制 shot_type 多样性 + 首格 wide + 群像 group。

    in-place 修改 pages。返回修改统计(供 logging)。

    强制规则(对齐 screenwriter v3 铁律 2/3/4):
      ① 首格(panel_index=1)如果不在 _WIDE_SHOT_TYPES → 强改为 "远景"
      ② 该页至少有 1 panel.characters >= 2 但所有 panel 都不在 _GROUP_SHOT_TYPES
        → 把"出现 >= 2 角色 + 当前 shot_type 不在 wide 类"的第一个 panel 改为 "群像"
      ③ 整页 6 panel 唯一 shot_type 种类 < 4 → 后部 panels 强制改成稀缺类型
        (优先改最末 panel 为 "特写",次末改 "近景")
    """
    stats = {"forced_first_wide": 0, "forced_group_shot": 0, "forced_diversity": 0, "pages": 0}
    if not pages:
        return stats

    for page in pages:
        if not isinstance(page, dict):
            continue
        panels = page.get("panels")
        if not isinstance(panels, list) or not panels:
            continue
        stats["pages"] += 1

        # ① 首格 wide
        first = panels[0] if panels else None
        if first is not None:
            cur_shot = first.get("shot_type") or ""
            if cur_shot not in _WIDE_SHOT_TYPES:
                first["shot_type"] = "远景"
                stats["forced_first_wide"] += 1

        # ② 群像场景强制
        has_multi_char = any(
            isinstance(p.get("characters"), list) and len(p["characters"]) >= 2
            for p in panels
        )
        has_group_shot = any(
            (p.get("shot_type") or "") in _GROUP_SHOT_TYPES for p in panels
        )
        if has_multi_char and not has_group_shot:
            # 找出"多角色 + 当前不是 wide 类" 的第一个 panel(避免覆盖首格 wide)
            for p in panels[1:]:   # 跳过首格
                chars = p.get("characters") or []
                if isinstance(chars, list) and len(chars) >= 2:
                    p["shot_type"] = "群像"
                    stats["forced_group_shot"] += 1
                    break

        # ③ 多样性兜底:种类 < 4 时强制散开
        unique_shots = {(p.get("shot_type") or "中景") for p in panels}
        if len(panels) >= 4 and len(unique_shots) < 4:
            # 把末 panel 改 "特写",次末改 "近景"(如果它们不在当前 unique set)
            tweaks = [("特写", -1), ("近景", -2), ("中景", -3)]
            for tweak_shot, idx in tweaks:
                if abs(idx) > len(panels):
                    break
                if tweak_shot not in unique_shots:
                    target = panels[idx]
                    target["shot_type"] = tweak_shot
                    unique_shots.add(tweak_shot)
                    stats["forced_diversity"] += 1
                    if len(unique_shots) >= 4:
                        break

    return stats


def _agent_scripter(comic: Comic, conn: sqlite3.Connection) -> dict:
    """Agent #2 编剧 — 文学语言 → 视觉剧本(分格脚本)。

    输入:source(simulation narratives OR upload file_text)+ 图谱角色名清单
    输出:script JSON,落 `comic_projects.script_json` 字段,返回 usage dict

    实现策略(Sprint 3 Phase 2 真分批承接,2026-05-13):
      - 拉 source 全文,截到 30K 字(token 控;DeepSeek V3 64K context 可放)
      - 按 target_pages / 6 切批,每批 ≤ 6 页 × 6 格
      - 每批 DeepSeek V3 单次调用,max_tokens=8000(避开 ~8K output 硬上限)
      - 批间 tail_context 承接:上批末尾 150-300 字摘要传下批,LLM 据此续写
      - JSON 解析失败 → 抛 ValueError,run_comic 顶层 catch 改 state='failed'

    历史:
      - Sprint 2.B+ 七修(2026-05-12):target_pages 默认 30 → 12,试图防 JSON 截断 — 失败
      - Sprint C.4(2026-05-13):target_pages 由 AI Planner 决定 + 滑块,仍单次调用 — 12 页仍截断
      - Sprint 3 Phase 2(2026-05-13):**真分批承接**,彻底解决 LLM 输出上限问题。

    成本估算(整本汇总):12 页(2 批)≈ ¥0.9 + 后续 generator 72 格 × ¥0.20 ≈ ¥15;
                          18 页(3 批)≈ ¥1.3 + 108 格 × ¥0.20 ≈ ¥22。
    """
    source_text = _load_source_text(conn, comic)
    if not source_text or len(source_text.strip()) < 100:
        raise ValueError(f"comic {comic.id} 输入源文本不足(< 100 字),无法编剧")

    # 截到 30K 字(GPT-3.5 级别 token 控;DeepSeek V3 实际 64K context 可放)
    truncated_text = source_text[:30000]
    truncation_note = (
        f"\n\n[本次编剧仅使用前 {len(truncated_text)} 字,完整原文 {len(source_text)} 字]"
        if len(source_text) > 30000 else ""
    )
    source_text_with_note = truncated_text + truncation_note

    # 图谱角色清单
    name_to_id = _load_character_id_map(conn, comic)
    char_names_text = "、".join(name_to_id.keys()) if name_to_id else "(外部文本,无图谱)"

    # 切批参数
    target_pages = comic.target_pages
    BATCH_SIZE = 6  # 每批最大页数(对齐 DeepSeek V3 ~8K output token 余量)
    num_batches = (target_pages + BATCH_SIZE - 1) // BATCH_SIZE  # ceil

    # 累加产物
    all_pages: list[dict] = []
    title: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    tail_context: str = ""  # 首批传空,后续批承接上一批

    # Sprint 5.x bug fix:scripting 阶段 progress 10 → 24,每批 +N%
    # extracting_visuals 阶段从 25 起,留 10 → 24 给 scripter 内部
    _SCRIPTER_PROGRESS_LO = 10
    _SCRIPTER_PROGRESS_HI = 24
    _scripter_step = (_SCRIPTER_PROGRESS_HI - _SCRIPTER_PROGRESS_LO) / max(num_batches, 1)

    for batch_idx in range(num_batches):
        batch_start = batch_idx * BATCH_SIZE + 1
        batch_end = min((batch_idx + 1) * BATCH_SIZE, target_pages)
        is_first = (batch_idx == 0)
        is_last = (batch_idx == num_batches - 1)

        batch_result, batch_usage = _scripter_batch(
            batch_start_page=batch_start,
            batch_end_page=batch_end,
            total_pages=target_pages,
            is_first_batch=is_first,
            is_last_batch=is_last,
            previous_tail_context=tail_context,
            source_text_with_note=source_text_with_note,
            char_names_text=char_names_text,
            name_to_id=name_to_id,
        )

        all_pages.extend(batch_result["pages"])
        if is_first:
            title = batch_result.get("title") or ""
        tail_context = batch_result.get("tail_context") or ""
        total_input_tokens += batch_usage["input_tokens"]
        total_output_tokens += batch_usage["output_tokens"]

        # 每批完成后更新 progress(用户看到平滑推进,而非"卡死 60s")
        _update_progress(
            conn, comic.id,
            int(_SCRIPTER_PROGRESS_LO + _scripter_step * (batch_idx + 1)),
        )

    # 合并产物(orchestrator 视角:对外仍是单 script JSON,接口不变)
    # Sprint 5.10(2026-05-14)工程层兜底:scripter LLM 大量违反 screenwriter v3 12 铁律
    # (实测全大头近景),程序强制改 shot_type 多样化(首格 wide / 群像 group / 4 种多样性)
    enforce_stats = _enforce_shot_diversity(all_pages)
    if any(v for k, v in enforce_stats.items() if k != "pages"):
        import logging
        logging.info(
            f"_enforce_shot_diversity for comic {comic.id}: "
            f"修首格 wide={enforce_stats['forced_first_wide']} / "
            f"补群像 group={enforce_stats['forced_group_shot']} / "
            f"散多样性={enforce_stats['forced_diversity']} "
            f"(共 {enforce_stats['pages']} 页)"
        )

    final_script = {
        "title": title or "(未命名)",
        "total_pages": target_pages,
        "pages": all_pages,
    }

    # 落 db
    execute(
        conn,
        "UPDATE comic_projects SET script_json=?, updated_at=? WHERE id=?",
        (json.dumps(final_script, ensure_ascii=False), _now_iso(), comic.id),
    )
    conn.commit()

    total_usage = {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "batches": num_batches,
    }

    # Sprint C.2:credit 真扣(编剧 LLM 调用按真实 token);Phase 2:多批汇总扣一次
    try:
        units = credit_units_for_text_call(total_input_tokens, total_output_tokens)
        cost_yuan = lookup_price(
            "deepseek", settings.llm_model, "token",
            total_input_tokens, total_output_tokens,
        )
        consume_credits(
            conn, user_id=comic.user_id, action="comic_scripter",
            units=units, related_id=comic.id, cost_yuan=cost_yuan,
            metadata={**total_usage, "vendor": "deepseek", "agent": "scripter"},
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"consume_credits(comic_scripter) failed for {comic.id}: {e}")

    return total_usage


def _agent_visual_assets_extractor(comic: Comic, conn: sqlite3.Connection) -> dict:
    """Agent #5 素材库抽取员 ⭐ v3 新 — 抽 character_visuals / scenes / props 三类。

    与 _agent_scripter **并行启动**(2 类工作流不互相阻塞)。
    输入:source 全本文本 + 图谱 character.name → id 映射
    输出:落 db 三表 character_visuals / scenes / props,返回 usage + counts

    实现策略(Sprint 2.A):
      - prompts/visual_assets_extractor.md(✅ 已就位,Sprint 0 实测 87.8% 填空率)
      - DeepSeek V3,温度 0.3(稳定优先),max_tokens=8000
      - source 截到 30K 字(token 控)
      - 抽到的角色名要在 image_id_by_name 里(用 _load_character_id_map),
        不命中的跳过(防 LLM 抽出原文未存在的角色)

    成本估算:¥1.5/全本(Sprint 0 Test 3 ¥0.0058 是节选,全本按 token 量推估)
    """
    source_text = _load_source_text(conn, comic)
    if not source_text or len(source_text.strip()) < 100:
        raise ValueError(f"comic {comic.id} 输入源文本不足,无法抽视觉素材")

    truncated_text = source_text[:30000]
    system_prompt = _load_prompt("visual_assets_extractor.md")
    # screenwriter prompt 末尾本身已有 "## 原文片段" header,直接拼用户输入
    user_input = "```\n" + truncated_text + "\n```"

    parsed, usage = _openai_compat_call_json(
        system_prompt=system_prompt,
        user_input=user_input,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        vendor_label="DeepSeek-VisualAssets",
        max_tokens=8000,
        temperature=0.3,
        timeout=120.0,
    )

    # 形态校验
    if not isinstance(parsed, dict):
        raise ValueError(
            f"素材库 LLM 输出非 dict:{type(parsed).__name__}"
        )
    for key in ("character_visuals", "scenes", "props"):
        if key in parsed and not isinstance(parsed[key], list):
            raise ValueError(
                f"素材库 LLM 输出 {key} 字段非数组:{type(parsed[key]).__name__}"
            )

    name_to_id = _load_character_id_map(conn, comic)
    counts = _save_visual_assets(conn, comic.id, parsed, name_to_id)

    # Sprint C.2:credit 真扣
    try:
        units = credit_units_for_text_call(usage["input_tokens"], usage["output_tokens"])
        cost_yuan = lookup_price(
            "deepseek", settings.llm_model, "token",
            usage["input_tokens"], usage["output_tokens"],
        )
        consume_credits(
            conn, user_id=comic.user_id, action="comic_visual_assets",
            units=units, related_id=comic.id, cost_yuan=cost_yuan,
            metadata={**usage, "vendor": "deepseek", "agent": "visual_assets_extractor", "counts": counts},
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"consume_credits(comic_visual_assets) failed for {comic.id}: {e}")

    # Sprint 5.x bug fix:extracting_visuals 完成 → progress 推到 44(下一步 style 起点 50)
    _update_progress(conn, comic.id, 44)

    return {**usage, "counts": counts}


def _agent_style_director_v2(
    comic: Comic,
    conn: sqlite3.Connection,
    user_uploaded_image_urls: list[str],
) -> dict:
    """Agent #3 v3 画风定调员 — 多模态视觉 DNA(12 字段)+ 详细 prompt + 自适应 negative + 3 张多样化候选。

    Sprint 5.4(2026-05-13)从 v2 升级到 v3,基于用户实测痛点 + 多智能体 AI 漫画工作流研究:
      Stage 1:Qwen-VL Max × 3 提取 12 字段视觉 DNA(v2 是 8 字段,v3 加 mood / atmosphere
              / shadow_tone / inspirations)+ **内容-风格解耦铁律**(content leakage 防御)
      Stage 2:DeepSeek V3 综合 → detailed_prompt(头部锚 mood)+ **自适应 negative_prompt**
              + **3 张多维差异化候选**(笔触/氛围/构图,非仅色温梯度)
      Stage 3:Seedream 4.0 用 detailed_prompt + variant_hint 出 3 张 1:1 定调样张

    v2 → v3 修复了用户报"诡异题材出水彩清新"问题(根因:mood 维度缺失 + LLM 默认偏好通用画风)

    Sprint 5.4.1(2026-05-13)加"跳过"模式:
      - user_uploaded_image_urls=[] → 跳过 Stage 1 Qwen-VL,Stage 2 仅从 source_text + 题材推画风
      - 用户用例:没有现成参考图,愿让 AI 从题材自动推断
      - 成本:Seedream 3×¥0.20 + DeepSeek ¥0.01 ≈ ¥0.61(省 Qwen-VL 的 ¥0.045)
      - 质量:略低于有图模式(LLM 无视觉锚 → 题材匹配画风,准确度依赖 prompt 质量)

    落 db:更新 comic_projects 多个 style_* 字段
      - style_detailed_prompt = detailed_prompt + "\n\n" + negative_prompt(尾部拼接自适应 negative)
      - 这样 director_v2 拉 detailed_prompt 时自动带上 mood 反义 negative,无需 schema 改

    成本估算(有图):Qwen-VL 3×¥0.015 + DeepSeek ¥0.01 + Seedream 3×¥0.20 ≈ ¥0.66
    成本估算(跳过):DeepSeek ¥0.01 + Seedream 3×¥0.20 ≈ ¥0.61
    """
    if len(user_uploaded_image_urls) not in (0, 3):
        raise ValueError(
            f"画风定调员 v2 需要 0 张(跳过模式)或 3 张(有图模式),"
            f"收到 {len(user_uploaded_image_urls)} 张"
        )

    # Sprint 5.4.1:text_only 模式 = 0 张,跳过 Stage 1
    text_only_mode = len(user_uploaded_image_urls) == 0

    # ===== Stage 1:Qwen-VL Max × 3 视觉 DNA 提取(v3:12 字段 + 内容-风格解耦铁律) =====
    qwen_dna_prompt = """你是视觉 DNA 提取员。看下面这张图,**精确分析画风视觉特征**,
严格按 JSON 模板输出(只输出 JSON,无任何前后文)。

⚠️ 内容-风格解耦铁律(违反即整次输出作废):
- 绝对禁止描述图中的具体内容主体(人物、物件、场景、衣着)
- 你的输出必须是 100% 内容无关(Content-Agnostic)
- 只提取美学属性(笔触/上色/光影/氛围),不提任何具体的"猫、人、城堡、衣服"

输出 JSON(12 字段全填):

```json
{
  "brush_style": "厚涂 | 半厚涂 | 赛璐璐 | 扁平 | 水彩 | 水墨 | 其他",
  "coloring": "高饱和 | 莫兰迪 | 暗色调 | 明亮 | 复古 | 其他",
  "line_work": "清晰勾线 | 朦胧线 | 无线 | 其他",
  "character_proportion": "日漫大眼 | Q版 | 写实 | 国漫 | 其他",
  "lighting_logic": "强对比 | 扁平 | 氛围光 | 逆光 | 其他",
  "composition": "中近景 | 远景 | 特写 | 全景 | 其他",
  "color_palette": ["主色1", "主色2", "主色3"],
  "mood": "压抑 | 治愈 | 紧张 | 浪漫 | 宁静 | 诡异 | 史诗 | 玩闹 | 其他",
  "atmosphere": "<描述性,1-2 个词组,如 '夜晚潮湿' / '阳光散射' / '雾气神秘' / '冷峻金属感'>",
  "shadow_tone": "蓝紫冷调 | 橙褐暖调 | 中性灰 | 黑深无色 | 其他",
  "inspirations": ["<风格 reference 1>", "<风格 reference 2>"],
  "overall_style_tag": "<整体画风一句话,20 字内,纯美学描述,不提具体内容>"
}
```

铁律:
- 必填 12 字段,不漏(不确定填 "其他" 或 [])
- color_palette 限 2-3 个主色(中文)
- inspirations 限 1-3 个风格 reference(如 "押井守 / Studio Ghibli / 新黑色电影漫画风")
- overall_style_tag 20 字内**纯美学描述**(如"国漫工笔半厚涂 + 诡异暗调")
"""
    visual_dnas: list[dict] = []
    qwen_total_tokens = 0
    # Sprint 5.4.1:text_only 模式整段跳 Stage 1(无图,visual_dnas 留空数组)
    if text_only_mode:
        vision_llm = None
        # text_only:跳 Stage 1,progress 直推到 Stage 2 起点(55%)
        _update_progress(conn, comic.id, 55)
    else:
        vision_llm = get_vision_llm()
    # Sprint 5.x bug fix:style_analyzing 阶段 progress 50 → 55(Stage 1 Qwen-VL × 3 张图)
    _STAGE1_LO, _STAGE1_HI = 50, 55
    for idx, url in enumerate(user_uploaded_image_urls, start=1):
        # Sprint 2.B+ 六修(2026-05-12):本地上传 URL → base64 data URL
        # Qwen-VL 服务器无法拉 /api/comic-files/ 内部路径,本地图必须转 data URL
        real_url = _resolve_image_url_for_vision(url)
        answer, usage = vision_llm.describe(real_url, qwen_dna_prompt, max_tokens=1000)
        qwen_total_tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        try:
            dna = json.loads(_strip_markdown_fence(answer))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Qwen-VL 视觉 DNA 输出 JSON 解析失败:{e};raw 前 200 字={answer[:200]!r}"
            ) from e
        visual_dnas.append(dna)
        # 每张 DNA 提取完更新进度(3 张 → 50/52/54/55)
        _update_progress(
            conn, comic.id,
            int(_STAGE1_LO + (_STAGE1_HI - _STAGE1_LO) * idx / max(len(user_uploaded_image_urls), 1)),
        )

    # ===== Stage 2:DeepSeek 综合 → 详细 prompt + 3 候选 =====
    # 拉剧本摘要(已由 Agent #2 落 script_json)+ 图谱核心
    name_to_id = _load_character_id_map(conn, comic)
    char_names = "、".join(name_to_id.keys()) if name_to_id else "(未知)"
    script_summary = (
        f"作品涉及角色:{char_names}。"
        f"剧本由编剧 agent 已生成 {len(comic.script.get('pages', [])) if comic.script else 0} 页。"
    )

    # Sprint 5.4.1(2026-05-13):text_only 模式额外注入 source_text 前 3K 字
    # 供 LLM 从题材 / 关键剧情场景推画风(没有视觉锚时唯一可参照的语义信息)
    source_text_excerpt = ""
    if text_only_mode:
        try:
            full_source = _load_source_text(conn, comic) or ""
            source_text_excerpt = full_source[:3000]
        except Exception:  # noqa: BLE001
            source_text_excerpt = ""

    # Sprint 5.4(2026-05-13):v2 → v3,改 prompt 文件路径
    synth_system = _load_prompt("style_director_v3.md")
    synth_user_input = {
        "visual_dnas": visual_dnas,
        "script_summary": script_summary,
        "graph_summary": f"角色 {len(name_to_id)} 个",
        # Sprint 5.4.1:无图模式标记 + 原文摘要(让 LLM 走"题材→mood→画风推断"路径)
        "mode": "text_only" if text_only_mode else "with_references",
        "source_text_excerpt": source_text_excerpt,
    }
    # Sprint 5.x bug fix:Stage 2 起点
    _update_progress(conn, comic.id, 55)
    synth_parsed, synth_usage = _openai_compat_call_json(
        system_prompt=synth_system,
        user_input=synth_user_input,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        vendor_label="DeepSeek-StyleSynth-v3",
        max_tokens=3000,
        temperature=0.6,
        timeout=60.0,
    )
    # Stage 2 完成 → 60(下面 Stage 3 60→68)
    _update_progress(conn, comic.id, 60)

    detailed_prompt = synth_parsed.get("style_detailed_prompt")
    style_tag = synth_parsed.get("style_tag")
    variants = synth_parsed.get("candidate_variants") or []
    # Sprint 5.4 v3 新增:自适应 negative_prompt(基于 mood 反推)
    # 兜底:若 LLM 未输出(v2 → v3 平滑迁移),用通用 negative
    negative_prompt = synth_parsed.get("negative_prompt") or (
        "禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感,"
        "禁止现代摄影构图,禁止文字水印"
    )
    if not detailed_prompt or len(detailed_prompt) < 100:
        raise ValueError(
            f"画风综合 LLM 输出 detailed_prompt 不足 100 字:{detailed_prompt!r}"
        )

    # Sprint 5.4(2026-05-13):把自适应 negative 拼到 detailed_prompt 尾部,这样 director
    # 拉 style_detailed_prompt 时自动带上 mood 反义 negative,无需 db schema 改。
    # 注:director_v2 prompt 自己也加通用 negative,会有少量重复但不冲突;
    #     未来 Sprint 可改 director_v2 删硬编 negative 让 v3 自适应 negative 独占。
    detailed_prompt_with_negative = (
        f"{detailed_prompt.rstrip()}\n\n{negative_prompt}"
    )
    # Sprint 2.B+ 七修(2026-05-12):候选数 5 → 3
    #   - 控成本(Seedream 单本省 ¥0.4)
    #   - 3 张已足够给用户做画风选择(测试发现 5 张选择疲劳)
    #   - 同步改 prompts/style_director_v2.md + VoteStyleRequest le=3 + 前端 grid
    if len(variants) != 3:
        raise ValueError(
            f"画风综合 LLM 输出 variants 数 != 3:实际 {len(variants)}"
        )

    # ===== Stage 3:Seedream × 3 出定调样张(Sprint 5.4 v3:用合并后 prompt + variant) =====
    # Sprint 5.x bug fix:Stage 3 progress 60 → 68(每张图 +N%)
    _STAGE3_LO, _STAGE3_HI = 60, 68
    image_gen = get_image_gen()
    candidates: list[dict] = []
    image_cost_per = 0.20   # Seedream 单价
    for i, variant_hint in enumerate(variants, 1):
        # 拼:detailed_prompt(已含自适应 negative)+ 变体差异提示
        panel_prompt = f"{detailed_prompt_with_negative}\n\n本张差异提示:{variant_hint}"
        try:
            # Sprint 4.D+(2026-05-13):候选样张改 1:1 — 与最终漫画 panel 比例一致,
            # 用户预览所见即所得(原 3:4 让用户误判,实际生成时格子被裁)
            result = image_gen.generate(panel_prompt, aspect_ratio="1:1")
            candidates.append({
                "index": i,
                "variant_hint": variant_hint,
                "image_url": result.url,
            })
        except Exception as e:
            # 单张失败不阻塞,留位置
            candidates.append({
                "index": i,
                "variant_hint": variant_hint,
                "image_url": None,
                "error": f"{type(e).__name__}: {str(e)[:100]}",
            })
        # 每张候选完成更新 progress(3 张 → 60/62.66/65.33/68)
        _update_progress(
            conn, comic.id,
            int(_STAGE3_LO + (_STAGE3_HI - _STAGE3_LO) * i / 3),
        )

    # ===== 落 db(Sprint 5.4 v3:存合并 prompt;director 拉时自带自适应 negative) =====
    execute(
        conn,
        """UPDATE comic_projects SET
            style_reference_image_urls_json=?,
            style_visual_dna_json=?,
            style_detailed_prompt=?,
            style_tag=?,
            style_candidates_json=?,
            updated_at=?
        WHERE id=?""",
        (
            json.dumps(user_uploaded_image_urls, ensure_ascii=False),
            json.dumps(visual_dnas, ensure_ascii=False),
            detailed_prompt_with_negative,    # v3:含自适应 negative 尾部
            style_tag,
            json.dumps(candidates, ensure_ascii=False),
            _now_iso(),
            comic.id,
        ),
    )
    conn.commit()

    # Sprint 5.B(2026-05-18)漫画态降级:不再扣 credit;改为运维 cost audit log
    # 用户视角"漫画态免费"由 enforce_comic_count_quota 控制本月次数,vendor 实际花费仅平台运维记录
    images_generated = sum(1 for c in candidates if c.get("image_url"))
    images_failed = sum(1 for c in candidates if not c.get("image_url"))
    try:
        import logging
        synth_in = synth_usage.get("input_tokens", 0)
        synth_out = synth_usage.get("output_tokens", 0)
        platform_cost_yuan = 0.0
        if qwen_total_tokens > 0:
            platform_cost_yuan += lookup_price(
                "qwen_vl", settings.qwen_vl_model, "token", qwen_total_tokens, 0,
            )
        if synth_in or synth_out:
            platform_cost_yuan += lookup_price(
                "deepseek", settings.llm_model, "token", synth_in, synth_out,
            )
        if images_generated > 0:
            platform_cost_yuan += lookup_price(
                "jimeng", settings.jimeng_model, "image",
                0, 0, image_count=images_generated,
            )
        logging.info(
            f"[comic style_director cost audit] comic={comic.id} user={comic.user_id} "
            f"vision_tokens={qwen_total_tokens} synth_tokens={synth_in}/{synth_out} "
            f"candidates_ok={images_generated} candidates_failed={images_failed} "
            f"platform_cost_yuan={platform_cost_yuan:.4f} "
            f"(Sprint 5.B: 不计入用户 credit)"
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"[comic style_director cost audit] failed for {comic.id}: {e}")

    return {
        "qwen_total_tokens": qwen_total_tokens,
        "synth_input_tokens": synth_usage.get("input_tokens", 0),
        "synth_output_tokens": synth_usage.get("output_tokens", 0),
        "images_generated": images_generated,
        "images_failed": images_failed,
    }


def _extract_script_characters(comic: Comic) -> list[str]:
    """Sprint 5.8 工程债 #1(2026-05-13):从 comic.script 提取实际在 panel 出场的角色名。

    返回按"出场频次降序 → 名字升序"排序的角色名列表(去重 + 严格剔除 falsy)。
    这是 character_anchor 真正应该覆盖的角色集合,**不是**图谱里的 top_n。

    根因(用户实测):
      - 旧 character_anchor 取图谱 top_n=6,但 scripter 自由提取原文角色名(常 7+ 个)
      - script 里的"李宇天 / 何雯 / 莫晴雨"等不在图谱 → director 拼 prompt 时
        character_descriptors=[] → Seedream 自由发挥 → 同名不同脸
      - 修法:character_anchor 改为"扫 script.pages[*].panels[*].characters 全锚定"
    """
    if not comic.script or "pages" not in comic.script:
        return []
    pages = comic.script.get("pages") or []
    if not isinstance(pages, list):
        return []

    counter: dict[str, int] = {}
    for page in pages:
        panels = (page or {}).get("panels") or []
        if not isinstance(panels, list):
            continue
        for panel in panels:
            chars = (panel or {}).get("characters") or []
            if not isinstance(chars, list):
                continue
            for name in chars:
                if not isinstance(name, str):
                    continue
                clean = name.strip()
                if not clean:
                    continue
                counter[clean] = counter.get(clean, 0) + 1

    # 频次降序 → 名字升序(确定性,便于测试)
    return sorted(counter.keys(), key=lambda n: (-counter[n], n))


def _extract_character_context_from_source(
    source_text: str, character_name: str, max_chars: int = 2000
) -> str:
    """Sprint 5.8 工程债 #1:对图谱外的 script-only 角色,从原文里抓取该角色出场上下文。

    简化策略:grep 字符匹配,围绕每次出现取前后 80 字,拼成最多 max_chars 的上下文片段。
    供 character_anchor LLM 推断该角色外貌(没图谱 character_visuals 数据时的兜底)。

    返回上下文文本(可能为空字符串,表示原文里也没提)。
    """
    if not source_text or not character_name:
        return ""
    snippets: list[str] = []
    start = 0
    total_chars = 0
    WINDOW = 80
    while total_chars < max_chars:
        idx = source_text.find(character_name, start)
        if idx == -1:
            break
        left = max(0, idx - WINDOW)
        right = min(len(source_text), idx + len(character_name) + WINDOW)
        snippets.append(source_text[left:right])
        total_chars += right - left
        start = right
        if len(snippets) >= 15:   # 上限 15 个片段,防极端长文本
            break
    return "\n...\n".join(snippets)


# ============================================================
# Sprint 5.10+(2026-05-14)Stage 3 重试 — 不取消漫画,只重出 3 张候选
# ============================================================
#
# 背景:用户实测后 Stage 3 候选 3 张可能全失败(vendor 限流 / 内容审核 / prompt 错),
#       原流程让用户"取消漫画 → 重新上传参考图"重走 30-60s,体验差
# 修法:抽 helper 只跑 Stage 3,前提是 db 里 style_detailed_prompt + candidates 已存在
#       (Stage 1 + 2 已完成的产物);加端点 POST /comics/{id}/retry_style_candidates


def _run_style_candidates_stage(
    comic: Comic, conn: sqlite3.Connection,
) -> dict:
    """Sprint 5.10+(2026-05-14):仅重跑 Stage 3 出 3 张候选样张。

    前提条件:
      - comic.style_detailed_prompt 已存在(Stage 2 已完成)
      - comic.style_candidates 有 3 个 variant_hint(可能 image_url 全 None)

    返回:{
        "created": int,        # 本次成功数
        "failed": int,         # 本次失败数
        "candidates": list,    # 新的 candidates list(落 db)
        "errors": list[str],   # 失败的 error 字段(供前端排错)
    }

    失败兜底:全 3 张都失败时,**仍落 db**(image_url=None + error),
              orchestrator 决定是否抛 ValueError(让 router 返 503)。
    """
    if not comic.style_detailed_prompt:
        raise ValueError(
            f"comic {comic.id} 无 style_detailed_prompt,Stage 1+2 未完成,无法重跑 Stage 3"
        )

    # 拉已有 variants(Stage 2 LLM 给的 3 个差异提示)
    existing = comic.style_candidates or []
    variants = [c.get("variant_hint") or "" for c in existing if c.get("variant_hint")]
    if len(variants) != 3:
        # 兜底:用预设(防 db 数据不齐全时仍能重跑)
        variants = [
            "重氛围光 + 近景人物特写 + 笔触细腻",
            "中性平衡 + 中景人物 + 环境清晰",
            "强光影对比 + 远景剪影 + 笔触豪放",
        ]

    image_gen = get_image_gen()
    candidates: list[dict] = []
    detailed_prompt_with_negative = comic.style_detailed_prompt
    errors: list[str] = []

    for i, variant_hint in enumerate(variants, 1):
        panel_prompt = f"{detailed_prompt_with_negative}\n\n本张差异提示:{variant_hint}"
        try:
            result = image_gen.generate(panel_prompt, aspect_ratio="1:1")
            candidates.append({
                "index": i,
                "variant_hint": variant_hint,
                "image_url": result.url,
            })
        except Exception as e:  # noqa: BLE001
            err_msg = f"{type(e).__name__}: {str(e)[:150]}"
            candidates.append({
                "index": i,
                "variant_hint": variant_hint,
                "image_url": None,
                "error": err_msg,
            })
            errors.append(err_msg)

    # 落 db:仅更新 style_candidates_json(其他 style_* 字段不动)
    execute(
        conn,
        "UPDATE comic_projects SET style_candidates_json=?, updated_at=? WHERE id=?",
        (
            json.dumps(candidates, ensure_ascii=False),
            _now_iso(),
            comic.id,
        ),
    )
    conn.commit()

    created = sum(1 for c in candidates if c.get("image_url"))
    failed = sum(1 for c in candidates if not c.get("image_url"))

    # Sprint 5.B(2026-05-18)漫画态降级:retry 不再扣 credit,仅 cost audit log
    if created > 0:
        try:
            import logging
            retry_cost_yuan = lookup_price(
                "jimeng", settings.jimeng_model, "image",
                0, 0, image_count=created,
            )
            logging.info(
                f"[comic style_retry cost audit] comic={comic.id} user={comic.user_id} "
                f"created={created} failed={failed} "
                f"platform_cost_yuan={retry_cost_yuan:.4f} "
                f"(Sprint 5.B: 不计入用户 credit)"
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.warning(f"[comic style_retry cost audit] failed for {comic.id}: {e}")

    return {
        "created": created,
        "failed": failed,
        "candidates": candidates,
        "errors": errors,
    }


def _agent_character_anchor(
    comic: Comic,
    conn: sqlite3.Connection,
    top_n: int = 20,
) -> dict:
    """Agent #4 角色锚定员 — 20-30 句描述符 + Seedream 立绘卡。

    Sprint 5.8 工程债 #1 重写(2026-05-13):
      原 Sprint 2.A 简化 — 取图谱 name_to_id 字典前 top_n=6 个角色锚定。
      但 scripter 自由从原文提取角色名(如「致命冲动」实际 7+ 角色),
      超出 6 的或图谱外的角色 → director 拉不到 descriptor → LLM 自由编造 →
      同名不同脸(用户实测平均跨 panel 一致性仅 26.6%,Gemini 评"角色一致性崩塌")。

    新策略(2026-05-13):
      1. 优先从 comic.script.pages[*].panels[*].characters 提取**实际出场角色全集**
      2. 与图谱 name_to_id 交集 → 走原路径(拉 character_visuals + characters 表档案)
      3. 与图谱不交集的 script-only 角色 → character_id 用 `script:<hash>` 前缀,
         从 source_text 抓出场上下文作 LLM 输入(无 character_visuals 数据,
         让 LLM 从 name + context 推断外貌,标 inferred=true)
      4. 总数硬上限 top_n=20(防 LLM 误抽 50+ 角色 credit 爆炸)

    成本:典型 8-12 角色 × (¥0.005 DeepSeek + ¥0.20 Seedream) ≈ ¥1.6-2.5 / 漫画
          (原 6 角色 ¥1.23,贵 ¥0.4-1.3,换 4× 角色一致性)
    """
    if not comic.style_detailed_prompt:
        raise ValueError(
            f"comic {comic.id} 没有 style_detailed_prompt,先跑画风定调员 v2"
        )

    # === Sprint 5.8 工程债 #1:扫 script 真实出场角色集合 ===
    script_chars = _extract_script_characters(comic)
    name_to_id = _load_character_id_map(conn, comic)

    # 组装锚定任务列表:(name, character_id, is_graph_known)
    anchor_tasks: list[tuple[str, str, bool]] = []
    seen_names: set[str] = set()

    # 优先 script 角色(出场频次降序);其次图谱里还没在 script 出场的(完整覆盖)
    for name in script_chars:
        if name in seen_names:
            continue
        seen_names.add(name)
        if name in name_to_id:
            anchor_tasks.append((name, name_to_id[name], True))
        else:
            # script-only 角色:用 hash 前缀防与图谱 id 冲突,本作品内 unique
            import hashlib
            stable_hash = hashlib.md5(f"{comic.id}|{name}".encode("utf-8")).hexdigest()[:16]
            anchor_tasks.append((name, f"script:{stable_hash}", False))

    # 图谱角色但 script 没用到(可能是次要 NPC,仍锚定供后续 inpainter 用)
    for name, cid in name_to_id.items():
        if name not in seen_names:
            seen_names.add(name)
            anchor_tasks.append((name, cid, True))

    # 上限保护
    if len(anchor_tasks) > top_n:
        # 优先保留 script 角色(已按频次排序在前),超出的图谱次要角色丢弃
        anchor_tasks = anchor_tasks[:top_n]

    if not anchor_tasks:
        raise ValueError(
            f"comic {comic.id} 无可锚定角色 — script 和图谱都为空"
        )

    # 加载原文(给 script-only 角色推断用)
    try:
        source_text = _load_source_text(conn, comic) or ""
    except Exception:  # noqa: BLE001
        source_text = ""

    system_prompt = _load_prompt("character_anchor_v2.md")
    image_gen = get_image_gen()
    now = _now_iso()

    counts = {"created": 0, "failed": 0, "graph_known": 0, "script_only": 0}
    total_descriptor_tokens = 0
    # Sprint 5.x bug fix:character_anchoring progress 80 → 84(每个角色 +N%)
    _ANCHOR_LO, _ANCHOR_HI = 80, 84
    _anchor_total = max(len(anchor_tasks), 1)
    for _anchor_idx, (character_name, character_id, is_graph_known) in enumerate(anchor_tasks, start=1):
        if is_graph_known:
            counts["graph_known"] += 1
        else:
            counts["script_only"] += 1

        # === 拉 character_visuals(仅图谱角色可能有数据) ===
        cv_dict = {
            "face_json": {},
            "hair_json": {},
            "body_json": {},
            "outfit_json": {},
            "accessories_json": [],
            "signature_props_json": [],
            "soul_traits": None,
        }
        if is_graph_known:
            cv_row = fetch_one(
                conn,
                "SELECT * FROM character_visuals WHERE comic_id=? AND character_id=?",
                (comic.id, character_id),
            )
            if cv_row:
                cv_dict = {
                    "face_json": json.loads(cv_row["face_json"] or "{}"),
                    "hair_json": json.loads(cv_row["hair_json"] or "{}"),
                    "body_json": json.loads(cv_row["body_json"] or "{}"),
                    "outfit_json": json.loads(cv_row["outfit_json"] or "{}"),
                    "accessories_json": json.loads(cv_row["accessories_json"] or "[]"),
                    "signature_props_json": json.loads(cv_row["signature_props_json"] or "[]"),
                    "soul_traits": cv_row["soul_traits"],
                }

        # === 拉 character 基础档案(仅图谱角色) ===
        char_basic = {
            "id": character_id,
            "name": character_name,
            "identity": "",
            "personality": "",
        }
        if is_graph_known:
            char_row = fetch_one(
                conn,
                "SELECT name, identity, personality FROM characters WHERE id=?",
                (character_id,),
            )
            if char_row:
                char_basic = {
                    "id": character_id,
                    "name": char_row["name"] or character_name,
                    "identity": char_row["identity"] or "",
                    "personality": char_row["personality"] or "",
                }

        # === Sprint 5.8 工程债 #1:script-only 角色补 source_text 上下文 ===
        source_context = ""
        if not is_graph_known and source_text:
            source_context = _extract_character_context_from_source(
                source_text, character_name, max_chars=1500,
            )

        # ===== DeepSeek 出 20-30 句 descriptor =====
        user_input = {
            "character": char_basic,
            "character_visuals": cv_dict,
            "style_detailed_prompt": comic.style_detailed_prompt[:500],   # 截断省 token
            # Sprint 5.8:script-only 角色无图谱数据,LLM 从原文上下文推断
            "source_context": source_context if not is_graph_known else "",
            "is_inferred_from_script": not is_graph_known,
        }
        try:
            descriptor_parsed, desc_usage = _openai_compat_call_json(
                system_prompt=system_prompt,
                user_input=user_input,
                api_key=settings.llm_api_key,
                api_base=settings.llm_api_base,
                model=settings.llm_model,
                vendor_label="DeepSeek-CharacterAnchor",
                max_tokens=2000,
                temperature=0.4,
                timeout=60.0,
            )
            descriptor = descriptor_parsed.get("descriptor", "")
            total_descriptor_tokens += (
                desc_usage.get("input_tokens", 0) + desc_usage.get("output_tokens", 0)
            )
            if not descriptor or len(descriptor) < 50:
                raise ValueError(f"descriptor 太短:{len(descriptor)} 字")
        except Exception as e:
            counts["failed"] += 1
            # 单角色失败不阻塞,跳过该角色
            continue

        # ===== Seedream 出立绘卡 =====
        try:
            anchor_prompt = (
                f"{comic.style_detailed_prompt}\n\n"
                f"角色描述:{descriptor}\n\n"
                f"正面立绘,中景半身,中性表情,纯色背景。"
            )
            result = image_gen.generate(anchor_prompt, aspect_ratio="3:4")
            card_url = result.url
        except Exception as e:
            counts["failed"] += 1
            continue

        # ===== 落 db =====
        execute(
            conn,
            """INSERT INTO character_cards (
                id, comic_id, character_id, character_name,
                descriptor, card_image_url, regenerated_count,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(comic_id, character_id) DO UPDATE SET
                descriptor=excluded.descriptor,
                card_image_url=excluded.card_image_url,
                regenerated_count=character_cards.regenerated_count + 1,
                updated_at=excluded.updated_at""",
            (
                uuid.uuid4().hex, comic.id, character_id, char_basic["name"],
                descriptor, card_url,
                now, now,
            ),
        )
        conn.commit()
        counts["created"] += 1

        # 每个角色锚定完更新 progress(平滑推进 80→84)
        _update_progress(
            conn, comic.id,
            int(_ANCHOR_LO + (_ANCHOR_HI - _ANCHOR_LO) * _anchor_idx / _anchor_total),
        )

    if counts["created"] == 0:
        raise ValueError(
            f"角色锚定员全失败(0/{len(anchor_tasks)}),无可用立绘卡"
        )

    # Sprint C.2:credit 真扣(DeepSeek descriptor + Seedream 立绘 × created)
    # input_tokens 估算:descriptor 输出粗略按总输出 / 创建数 / 2 分配 → 简化:input ≈ output × 0.5
    try:
        # DeepSeek descriptor:实际 input 约为 output / 4(系统 prompt + 角色档案)
        desc_in_estimate = total_descriptor_tokens // 4
        desc_units = credit_units_for_text_call(desc_in_estimate, total_descriptor_tokens)
        desc_cost = lookup_price(
            "deepseek", settings.llm_model, "token",
            desc_in_estimate, total_descriptor_tokens,
        )
        if desc_units > 0:
            consume_credits(
                conn, user_id=comic.user_id, action="comic_anchor_descriptor",
                units=desc_units, related_id=comic.id, cost_yuan=desc_cost,
                metadata={
                    "vendor": "deepseek", "agent": "character_anchor", "stage": "descriptor",
                    "total_descriptor_tokens": total_descriptor_tokens,
                    "input_tokens_estimate": desc_in_estimate,
                },
            )

        # Seedream 立绘 × 真出成功数
        if counts["created"] > 0:
            img_units = credit_units_for_image_gen(counts["created"])
            img_cost = lookup_price(
                "jimeng", settings.jimeng_model, "image",
                0, 0, image_count=counts["created"],
            )
            consume_credits(
                conn, user_id=comic.user_id, action="comic_anchor_card",
                units=img_units, related_id=comic.id, cost_yuan=img_cost,
                metadata={
                    "vendor": "jimeng", "agent": "character_anchor", "stage": "card",
                    "cards_created": counts["created"],
                    "cards_failed": counts["failed"],
                    # Sprint 5.8:区分图谱角色 vs script-only 角色,审计用
                    "graph_known": counts["graph_known"],
                    "script_only": counts["script_only"],
                },
            )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"consume_credits(character_anchor) partial failure for {comic.id}: {e}")

    return {
        "descriptor_total_tokens": total_descriptor_tokens,
        "cards_created": counts["created"],
        "cards_failed": counts["failed"],
        # Sprint 5.8 工程债 #1:新增统计 — 区分图谱已有 vs script 新提的角色
        "graph_known": counts["graph_known"],
        "script_only": counts["script_only"],
        "total_anchored": counts["graph_known"] + counts["script_only"],
    }


def _agent_director(
    comic: Comic,
    conn: sqlite3.Connection,
    panel_dict: dict,
) -> tuple[str, dict]:
    """Agent #6 导演 / 分镜 — 给单格组装 200-400 字 Seedream prompt。

    Sprint 3(2026-05-13):落地。

    Args:
        comic: 含 style_detailed_prompt / generation_seed / script 等
        panel_dict: 单格 dict,含 page_index / panel_index / shot_type / scene /
                    characters / action / dialogues / narrator 字段(直接来自
                    script_json.pages[i].panels[j])

    Returns:
        (prompt_text, usage_dict) — prompt 200-400 字,usage 含 input/output tokens

    Raises:
        ValueError:LLM 输出非纯文本 / 长度严重越界(< 100 字 OR > 600 字)
    """
    if not comic.style_detailed_prompt:
        raise ValueError(f"comic {comic.id} 无 style_detailed_prompt,先跑画风定调员")

    # 1. 拉本格涉及角色的 descriptor
    char_names = panel_dict.get("characters") or []
    character_descriptors: list[dict] = []
    if char_names:
        ph = ",".join(["?"] * len(char_names))
        rows = fetch_all(
            conn,
            f"""SELECT cc.character_name AS name, cc.descriptor
                FROM character_cards cc
                WHERE cc.comic_id=? AND cc.character_name IN ({ph})""",
            (comic.id, *char_names),
        )
        for r in rows:
            if r["descriptor"]:
                character_descriptors.append({
                    "name": r["name"],
                    "descriptor": r["descriptor"],
                })

    # 2. 拉本格场景 info(从 scenes 表)
    scene_name = panel_dict.get("scene")
    scene_info: dict = {}
    if scene_name:
        scene_row = fetch_one(
            conn,
            "SELECT * FROM scenes WHERE comic_id=? AND name=? LIMIT 1",
            (comic.id, scene_name),
        )
        if scene_row:
            scene_info = {
                "lighting": scene_row["lighting"],
                "architecture_style": scene_row["architecture_style"],
                "key_props_json": _safe_json(scene_row["key_props_json"], []),
            }

    # 3. 拉关键道具(scene.key_props_json 列表里的 prop 名 → 查 props 表)
    props_info: list[dict] = []
    key_prop_names = scene_info.get("key_props_json", []) if scene_info else []
    if key_prop_names:
        ph = ",".join(["?"] * len(key_prop_names))
        prop_rows = fetch_all(
            conn,
            f"""SELECT name, visual_description FROM props
                WHERE comic_id=? AND name IN ({ph})""",
            (comic.id, *key_prop_names),
        )
        for r in prop_rows:
            props_info.append({
                "name": r["name"],
                "visual_description": r["visual_description"] or "",
            })

    # 4. 装配 user_input 给 LLM(Sprint 5.7:加 emotion 字段)
    user_input = {
        "style_anchor_prompt": comic.style_detailed_prompt,
        "panel": {
            "page_index": panel_dict.get("page_index"),
            "panel_index": panel_dict.get("panel_index"),
            "shot_type": panel_dict.get("shot_type") or "中景",
            "scene_name": scene_name,
            "characters": char_names,
            "action": panel_dict.get("action") or "",
            "narrator": panel_dict.get("narrator"),
            # Sprint 5.7:scripter v3 给的 emotion(8 选 1),director 翻为身体语言
            # 不存在时给 calm 兜底(防 panel 字段缺失时整次 LLM 输出报错)
            "emotion": panel_dict.get("emotion") or "calm",
        },
        "scene_info": scene_info,
        "character_descriptors": character_descriptors,
        "props_info": props_info,
    }

    # 5. 调 DeepSeek(纯文本输出,非 JSON)
    system_prompt = _load_prompt("director_v2.md")
    user_msg = json.dumps(user_input, ensure_ascii=False)
    from app.services.llm_client import _openai_compat_call_text
    prompt_text, usage = _openai_compat_call_text(
        system_prompt=system_prompt,
        user_input=user_msg,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        vendor_label="DeepSeek-Director",
        max_tokens=1000,
        temperature=0.5,
        timeout=45.0,
    )

    # 6. 校验
    prompt_text = (prompt_text or "").strip()
    char_count = len(prompt_text)
    if char_count < 100:
        raise ValueError(
            f"director 输出过短({char_count} 字 < 100),page={panel_dict.get('page_index')} "
            f"panel={panel_dict.get('panel_index')};raw={prompt_text!r}"
        )
    if char_count > 800:
        # 略超容忍(800 字内),截到 600 字
        prompt_text = prompt_text[:600] + "..."

    # 7. Sprint 5.8 工程债 #2(2026-05-13):Fingerprint 自检 + 程序补注
    # director_v2.md 铁律 2 要求"逐字搬运 descriptor",但 LLM 在多角色 + 长 prompt
    # 上下文紧张时偷偷压缩(用户实测张凡 60%、地府守门人 0% 注入率)。
    # 修法:程序级 fingerprint 校验,LLM 缩写时硬注回完整 descriptor。
    prompt_text, force_injected = _director_ensure_descriptors_injected(
        prompt_text, character_descriptors,
    )
    if force_injected:
        usage = dict(usage)
        usage["force_injected_characters"] = force_injected

    # 7.5 Sprint 5.10(2026-05-14)工程层强制:shot_type 英文摄影术语硬注入
    # 对应教训 #31(prompt 层 ≠ 工程层强制) — 复用 Sprint 5.8 #2 的"程序兜底"pattern
    panel_shot_type = panel_dict.get("shot_type")
    prompt_text, shot_injected = _director_ensure_shot_type_injected(
        prompt_text, panel_shot_type,
    )
    if shot_injected:
        if not isinstance(usage, dict):
            usage = dict(usage)
        usage["force_injected_shot_type"] = panel_shot_type

    return prompt_text, usage


# Sprint 5.10(2026-05-14):shot_type 中文 → 英文摄影术语映射(供 director 硬注入)
# 用户实测后发现:director LLM 即使 prompt 写"翻译 shot_type 到英文",
# 也常常省略英文术语 → 弱生图模型(Qwen-Image)看不懂"远景"中文,默认出近景大头
# 修法:LLM 输出后程序级强制头部注入英文摄影术语,生图模型必然能识别
_SHOT_TYPE_TO_EN_KEYWORDS: dict[str, str] = {
    "远景": "wide shot, full body in frame, environment dominates the composition",
    "全景": "establishing shot, panorama view, character as small element in scene",
    "中景": "medium shot, waist up",
    "近景": "close shot, chest up",
    "特写": "close-up, head and shoulders",
    "大特写": "extreme close-up, focus on facial features or hand details",
    "群像": "group shot, multiple characters in same frame with clear spatial positions",
    "过肩": "over-the-shoulder shot, foreground character back of head, background dialogue partner face",
    "俯视": "bird's-eye view, top-down angle, character appears small/vulnerable",
    "仰视": "low-angle shot, looking up at character, sense of power/intimidation",
}


def _director_ensure_shot_type_injected(
    prompt_text: str, shot_type: str | None,
) -> tuple[str, bool]:
    """Sprint 5.10(2026-05-14)工程层强制:director LLM 输出后程序级注入 shot_type 英文术语。

    根因:director_v2.md prompt 写了"shot_type 必须翻译成生图模型听得懂的话",
    但 DeepSeek V3 实测大量违反 — 用户实测产物显示生图模型未收到 wide / group_shot
    等英文关键词,默认全大头照(教训 #31:prompt 层 ≠ 工程层强制)。

    程序兜底:取 panel.shot_type 对应的英文摄影术语,检查 prompt 是否已含主关键词,
    不含 → 在 prompt 头部硬注入 `[CAMERA: <英文术语>]` 块,生图模型必然能识别。

    返回 (修正后的 prompt, 是否强制注入了)。

    与 _director_ensure_descriptors_injected 同 pattern(成功复用 Sprint 5.8 #2 经验)。
    """
    if not shot_type:
        return prompt_text, False
    en_keywords = _SHOT_TYPE_TO_EN_KEYWORDS.get(shot_type)
    if not en_keywords:
        return prompt_text, False

    # 取英文术语主关键词(逗号前第一个),不区分大小写检查 prompt 是否已含
    main_keyword = en_keywords.split(",")[0].strip().lower()
    if main_keyword in prompt_text.lower():
        return prompt_text, False

    # 头部硬注入(生图模型对开头 token 权重最高)
    return f"[CAMERA: {en_keywords}] {prompt_text}", True


def _director_ensure_descriptors_injected(
    prompt_text: str, character_descriptors: list[dict],
) -> tuple[str, list[str]]:
    """Sprint 5.8 工程债 #2(2026-05-13):
    Director LLM 输出 prompt 后,程序级校验每个 panel 角色的 descriptor 是否
    真"逐字搬运",未达标则在 prompt 尾部 negative cue 之前硬塞完整 descriptor。

    fingerprint 策略:取每个 descriptor 前 80 字作"身份指纹",**normalize 后**
    比对(去空白 / 标点轻容差),命中即认 LLM 没缩写。

    返回 (修正后的 prompt, 被强制注入的角色名列表)。空列表表示无需补救。
    """
    if not character_descriptors:
        return prompt_text, []

    def _normalize(s: str) -> str:
        # 去空白 + 全角→半角 + 大小写。保留中文字符。
        s = "".join(s.split())
        return s.lower()

    prompt_norm = _normalize(prompt_text)
    force_injected: list[str] = []
    missing_blocks: list[str] = []

    for desc_obj in character_descriptors:
        name = desc_obj.get("name") or ""
        full_desc = desc_obj.get("descriptor") or ""
        if not name or not full_desc or len(full_desc) < 30:
            continue
        # 用前 80 字作 fingerprint(够长保证唯一性,够短即使 LLM 微调措辞仍可能命中)
        fingerprint = _normalize(full_desc[:80])
        if fingerprint in prompt_norm:
            continue
        # 命中失败 → 该角色 descriptor 没"逐字搬运",硬补
        force_injected.append(name)
        missing_blocks.append(f"【角色「{name}」完整描述符:{full_desc}】")

    if not missing_blocks:
        return prompt_text, []

    # 拼接策略:把硬补 blocks 插入 negative cue 之前(尾部最后一段)
    # negative cue 启动词("禁止"打头);找最后一个"禁止"位置,在它之前插入
    NEGATIVE_PREFIX = "禁止"
    insert_at = prompt_text.rfind(NEGATIVE_PREFIX)
    補丁 = "\n" + "\n".join(missing_blocks) + "\n"
    if insert_at == -1:
        # 没找到 negative cue,直接追加到尾
        new_prompt = prompt_text + 補丁
    else:
        new_prompt = prompt_text[:insert_at] + 補丁 + prompt_text[insert_at:]

    return new_prompt, force_injected


# ============================================================
# Sprint 5.11 Reflexion(2026-05-14):Qwen-VL Max 视觉校验 + speaker-missing 重生
# ============================================================
#
# 推荐组合(2026-05-14 用户拍板):
#   1A — 重生上限 1 次(成本 +50%)
#   2B — 只 speaker_missing fail 触发(成本最低,先攻最痛 bug)
#   3A — verifier_result 落 panel JSON(comic_pages.panels_json 已是 JSON 字段,零 migration)
#
# 用户实测痛点(2026-05-14 截图):
#   panel.dialogues[*].speaker = "地府守门人" 但画面里只有举手机的女孩
#   → 生图模型把对话当背景设定吸进去了,画了"被对话提及的人"而不是"在说话的人"
#
# 解决路径:
#   出图后调 Qwen-VL Max 校验 speaker 是否在画面 → fail 时重生 1 次,
#   prompt 头部硬注入 `[CRITICAL: 画面必须出现「{speaker}」,descriptor: ...]`

def _agent_visual_verifier(
    comic: Comic,
    panel_dict: dict,
    image_url: str,
    conn: sqlite3.Connection,
) -> dict:
    """Sprint 5.11 Reflexion:Qwen-VL Max 校验 panel 图像 vs 剧本 fidelity。

    只校验:dialogues[*].speaker 是否真的出现在画面里(对应"画面与对话脱节"痛点)。
    无对话格(纯过场)或图像缺失 → 直接 skip,不调 vendor。

    返回 dict(永不抛 — 失败降级 skip,主流程继续):
      {
        "verdict": "pass" | "fail" | "skip",
        "fail_reasons": ["speaker_missing", ...] | [],
        "speakers_expected": [name, ...],
        "speakers_visible": [name, ...],
        "speakers_missing": [name, ...],
        "person_count": int,           # -1 = 未知 / 解析失败
        "verdict_brief": str,          # Qwen-VL 给的 50 字判断说明
        "usage": {input_tokens, output_tokens},
        "skip_reason": str | None,
      }

    成本:每张图 Qwen-VL Max 1 次调用 ~¥0.02(input ~500 token + output ~100 token)。
    """
    # 边界 1:无图像 → skip(image_gen 已失败,无校验意义)
    if not image_url:
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": [], "speakers_visible": [], "speakers_missing": [],
            "person_count": -1, "verdict_brief": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "skip_reason": "no_image",
        }

    # 边界 2:无对话 / 无 speaker → 纯过场,无 speaker 校验需求
    dialogues = panel_dict.get("dialogues") or []
    speakers_expected = sorted({
        d.get("speaker", "").strip()
        for d in dialogues
        if isinstance(d, dict) and (d.get("speaker") or "").strip()
    })
    if not speakers_expected:
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": [], "speakers_visible": [], "speakers_missing": [],
            "person_count": -1, "verdict_brief": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "skip_reason": "no_dialogue",
        }

    # 拉 speaker 的 descriptor(给 Qwen-VL 当"认人依据")
    placeholders = ",".join(["?"] * len(speakers_expected))
    rows = fetch_all(
        conn,
        f"SELECT character_name AS name, descriptor FROM character_cards "
        f"WHERE comic_id=? AND character_name IN ({placeholders})",
        (comic.id, *speakers_expected),
    )
    speakers_with_desc: list[dict] = []
    for r in rows:
        desc = (r["descriptor"] or "").strip()
        if desc:
            # descriptor 限 200 字(给 Qwen-VL 够用,省 token)
            speakers_with_desc.append({"name": r["name"], "descriptor": desc[:200]})

    # 没拿到任何 descriptor → 无法校验(LLM 无身份依据)→ skip
    if not speakers_with_desc:
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": speakers_expected,
            "speakers_visible": [], "speakers_missing": speakers_expected,
            "person_count": -1, "verdict_brief": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "skip_reason": "no_descriptors",
        }

    # 加载 prompt 模板(_load_prompt 失败 → skip,不阻塞主流程)
    try:
        verifier_system_prompt = _load_prompt("visual_verifier.md")
    except Exception as e:  # noqa: BLE001
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": speakers_expected,
            "speakers_visible": [], "speakers_missing": [],
            "person_count": -1, "verdict_brief": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "skip_reason": f"prompt_load_failed: {type(e).__name__}",
        }

    # 拼 user question(speakers JSON 给 Qwen-VL)
    user_question = (
        verifier_system_prompt
        + "\n\n## 本次任务输入\n\nexpected_speakers = "
        + json.dumps(speakers_with_desc, ensure_ascii=False, indent=2)
        + "\n\n现在请看图,按上述 JSON 格式输出校验结果。"
    )

    # 调 Qwen-VL Max
    vision_llm = get_vision_llm()
    real_url = _resolve_image_url_for_vision(image_url)
    try:
        answer, usage = vision_llm.describe(real_url, user_question, max_tokens=400)
    except Exception as e:  # noqa: BLE001
        # vendor 抖动 → skip(不重生不烧钱;主流程继续,verifier_result 留 audit 痕迹)
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": speakers_expected,
            "speakers_visible": [], "speakers_missing": [],
            "person_count": -1, "verdict_brief": "",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "skip_reason": f"vendor_error: {type(e).__name__}: {str(e)[:120]}",
        }

    # 解析 JSON(复用 3 级 fallback)
    from app.services.llm_client import _extract_json_block as _ej

    parsed = None
    try:
        parsed = json.loads(_strip_markdown_fence(answer))
    except (json.JSONDecodeError, ValueError):
        try:
            block = _ej(answer)
            if block:
                parsed = json.loads(block)
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = None

    if not isinstance(parsed, dict):
        return {
            "verdict": "skip", "fail_reasons": [],
            "speakers_expected": speakers_expected,
            "speakers_visible": [], "speakers_missing": [],
            "person_count": -1, "verdict_brief": "",
            "usage": usage,
            "skip_reason": f"parse_failed: raw_head={(answer or '')[:80]!r}",
        }

    # 抽 speakers_visible(Qwen-VL 的判定)
    visible_raw = parsed.get("speakers_visible") or []
    if not isinstance(visible_raw, list):
        visible_raw = []
    speakers_visible = sorted({
        str(v).strip() for v in visible_raw if v and str(v).strip()
    })

    # 与 expected 交集 / 差集
    expected_set = set(speakers_expected)
    visible_set = set(speakers_visible) & expected_set
    missing = sorted(expected_set - visible_set)

    person_count_raw = parsed.get("person_count")
    person_count = (
        int(person_count_raw)
        if isinstance(person_count_raw, int)
        or (isinstance(person_count_raw, str) and person_count_raw.lstrip("-").isdigit())
        else -1
    )

    verdict = "fail" if missing else "pass"
    fail_reasons = ["speaker_missing"] if missing else []

    return {
        "verdict": verdict,
        "fail_reasons": fail_reasons,
        "speakers_expected": speakers_expected,
        "speakers_visible": sorted(visible_set),
        "speakers_missing": missing,
        "person_count": person_count,
        "verdict_brief": str(parsed.get("verdict_brief") or "")[:200],
        "usage": usage,
        "skip_reason": None,
    }


def _build_speaker_grounding_injection(
    speakers_missing: list[str],
    speakers_with_desc: list[dict],
) -> str | None:
    """Sprint 5.11 helper:为缺失的 speaker 构造头部 CRITICAL 注入块。

    返回 `[CRITICAL: 角色「X」必须出现在画面中(说话人) — descriptor: ...]` 字符串,
    或 None(没有可用 descriptor)。

    抽出来独立函数便于单元测试(无 DB 依赖)。
    """
    if not speakers_missing or not speakers_with_desc:
        return None
    name_to_desc = {d.get("name"): (d.get("descriptor") or "")[:300] for d in speakers_with_desc}
    blocks: list[str] = []
    for name in speakers_missing:
        desc = name_to_desc.get(name)
        if desc:
            blocks.append(f"角色「{name}」必须出现在画面中并清晰可见(说话人) — {desc}")
    if not blocks:
        return None
    return "[CRITICAL: " + " | ".join(blocks) + "]"


def _maybe_regenerate_panel_for_speaker_missing(
    comic: Comic,
    panel_dict: dict,
    original_prompt: str,
    verifier_result: dict,
    conn: sqlite3.Connection,
) -> tuple[str | None, dict, str | None]:
    """Sprint 5.11 推荐组合 1A+2B:fail 时重生 1 次,只针对 speaker_missing。

    返回 (new_image_url, image_usage, new_prompt_used):
      - new_image_url=None → 没重生(verdict 非 fail / fail_reason 不在白名单 / 缺 descriptor)
      - new_image_url=str → 重生成功(或失败但流程不抛,new_image_url 可能仍是空)

    成本:每次重生 = 1 张 image_gen ¥0.5(CogView-4 标准价)
    """
    # 决策 1:只 fail 且 speaker_missing 触发
    if verifier_result.get("verdict") != "fail":
        return None, {}, None
    if "speaker_missing" not in (verifier_result.get("fail_reasons") or []):
        return None, {}, None

    missing = verifier_result.get("speakers_missing") or []
    if not missing:
        return None, {}, None

    # 拉缺失 speaker 的 descriptor(重生 prompt 注入用)
    placeholders = ",".join(["?"] * len(missing))
    rows = fetch_all(
        conn,
        f"SELECT character_name AS name, descriptor FROM character_cards "
        f"WHERE comic_id=? AND character_name IN ({placeholders})",
        (comic.id, *missing),
    )
    speakers_with_desc = [
        {"name": r["name"], "descriptor": r["descriptor"] or ""}
        for r in rows
        if (r["descriptor"] or "").strip()
    ]

    injection = _build_speaker_grounding_injection(missing, speakers_with_desc)
    if not injection:
        return None, {}, None

    new_prompt = f"{injection}\n\n{original_prompt}"

    # 重生 1 次(同 generation_seed → L4 角色一致性;新 prompt 头部注入 speaker grounding)
    new_url, img_usage = _agent_image_generator(
        comic, new_prompt, aspect_ratio="1:1",
    )
    return new_url, img_usage, new_prompt


def _safe_json(text: str | None, fallback):
    """容错 json.loads — Sprint 3 给 _agent_director 拉 scenes.key_props_json 用。"""
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _agent_image_generator(
    comic: Comic,
    prompt: str,
    aspect_ratio: str = "3:4",
) -> tuple[str | None, dict]:
    """Agent #7 图像生成 — 调 Seedream 4.0 出图。Sprint 3(2026-05-13)落地。

    4 层一致性集成:
      - L2:prompt 已塞角色 descriptor + scene + props(Agent #6 装配)
      - L4:整本同 generation_seed(从 comic.generation_seed 拉)
      - L1 / L3:留 vendor 升级时启用 ref_image

    Args:
        prompt:Agent #6 输出的 200-400 字完整 prompt
        aspect_ratio:漫画格典型 3:4(竖版),也可 4:3 / 16:9

    Returns:
        (image_url, usage_dict) — 失败时 image_url=None + usage 含 error 字段
        失败不抛(主循环 180 格里单格失败不应炸整本)— 失败的格 panels_json 里
        image_url=None,Sprint 4 inpainter 让用户补 / 重生。
    """
    image_gen = get_image_gen()
    try:
        result = image_gen.generate(
            prompt,
            aspect_ratio=aspect_ratio,
            seed=comic.generation_seed,    # L4 同 seed
        )
        return result.url, {
            "image_count": 1,
            "vendor": "jimeng",
            "model": settings.jimeng_model,
        }
    except Exception as e:  # noqa: BLE001
        # 单格失败不阻塞 — 留空位让 inpainter 后补
        return None, {
            "image_count": 0,
            "vendor": "jimeng",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }


def _agent_visual_qa(
    comic: Comic,
    image_url: str,
    expected: dict,
) -> dict:
    """Agent #8 视觉质检 — Qwen-VL Max 关键帧扫,返回评估 JSON。

    关键帧策略:每页首格 + 角色出场首格 = ~40/180 ≈ 22% 关键帧
                (省 78% 成本 vs 全帧扫)

    输入:产物 URL + expected({角色清单 / 服饰要点 / 场景要点})
    输出:{passes: bool, issues: [...], confidence: 0-100}

    Sprint 3 实施:
      - prompts/visual_qa.md(待写,参照 d8_evaluate 的 Qwen-VL prompt)
      - Qwen-VL Max × 40 次 / 整本
      - 估算成本:40 × ¥0.004 = ¥0.16 / 整本
    """
    raise NotImplementedError("Sprint 3 实施,详 ADR v3 §3.2 Agent #8")


def _agent_inpainter(
    comic: Comic,
    page_index: int,
    panel_index: int,
    user_instruction: str,
) -> str:
    """Agent #9 局部重绘 — 用户点"这格不对"触发,调 Seedream Edit / 通义万相 inpaint。

    输入:原图 URL + user_instruction(用户中文描述要改什么)
    输出:新图 URL(替换 comic_pages.panels_json[panel_index].image_url)

    Sprint 3 实施:
      - vendor 当前 Seedream Edit deprecated(Sprint 0 实测 404),
        D.9 实施时需找替代(seedream-image-prompt / 通义万相 inpaint)
      - 估算成本:用户触发,每次 ¥0.20
    """
    raise NotImplementedError("Sprint 3 实施,详 ADR v3 §3.2 Agent #9")


# ============================================================
# Sprint 4.C Typesetter — 字体 / 布局常量(2026-05-13)
# ============================================================

# 字体:Source Han Sans SC Regular(用户手动下载到 backend/assets/fonts/,Adobe + Google
# 开源 OFL 协议,中日韩文 + 拉丁字符全覆盖,~16MB 单字重)
_TYPESETTER_FONT_PATH = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "SourceHanSansSC-Regular.otf"

# Sprint 4.D+(2026-05-13)用户实测后改:整页 800 × 1200 px,与 reader grid 容器
#   aspect-ratio: 2/3 严格匹配(2 列 × 3 行 1:1 正方形 panel,对齐 Seedream "1:1" 生图比例)
# 原 800×1500 (3:5) + panel 3:4 导致 reader grid (1:1 容器格) cover 模式裁掉图片上下
# — 用户报"每张图片都不完整" → 改 panel 1:1 + 整页 2:3 比例消除裁切
_TS_PAGE_W = 800
_TS_PAGE_H = 1200
_TS_MARGIN = 8
_TS_GAP = 8
_TS_COLS = 2
_TS_ROWS = 3
# 派生:每格 388×389 ≈ 1:1(Seedream "1:1" 输出 1024×1024,object-fit:cover 几乎无裁)
_TS_PANEL_W = (_TS_PAGE_W - 2 * _TS_MARGIN - (_TS_COLS - 1) * _TS_GAP) // _TS_COLS  # 388
_TS_PANEL_H = (_TS_PAGE_H - 2 * _TS_MARGIN - (_TS_ROWS - 1) * _TS_GAP) // _TS_ROWS  # 389

# 字号 / 颜色 — 对齐 ComicReaderView 的视觉 token
_TS_FONT_SIZE_DIALOG = 16
_TS_FONT_SIZE_NARRATOR = 18
_TS_FONT_SIZE_SFX = 22
_TS_COLOR_BG = (255, 255, 255)            # 整页底色
_TS_COLOR_PANEL_BORDER = (220, 220, 220)
_TS_COLOR_FAILED_BG = (245, 230, 230)     # 失败格淡红底
_TS_COLOR_FAILED_TEXT = (190, 50, 50)
_TS_COLOR_NARRATOR_BG = (245, 208, 106)
_TS_COLOR_NARRATOR_TEXT = (74, 58, 0)
_TS_COLOR_DIALOG_BG = (252, 252, 252)
_TS_COLOR_DIALOG_TEXT = (26, 26, 26)
_TS_COLOR_DIALOG_SPEAKER = (75, 110, 175)  # 对齐 reader --color-accent
_TS_COLOR_SFX_BG = (224, 154, 47)
_TS_COLOR_SFX_TEXT = (255, 255, 255)


def _ts_load_fonts() -> dict:
    """加载字体(各字号独立 ImageFont 对象;失败抛 RuntimeError)。

    Sprint 4.C 字体策略 A:用户手动下载 SourceHanSansSC-Regular.otf 到
    backend/assets/fonts/,不内嵌到代码仓库(OFL 协议商用免费,但 16MB 不进 git)。
    部署时需把此文件随镜像一起 ship。
    """
    from PIL import ImageFont
    if not _TYPESETTER_FONT_PATH.exists():
        raise RuntimeError(
            f"字体文件缺失:{_TYPESETTER_FONT_PATH}。"
            f"请下载 Source Han Sans SC Regular OTF 放入此路径(Sprint 4.C 字体策略 A)。"
        )
    path_str = str(_TYPESETTER_FONT_PATH)
    return {
        "dialog": ImageFont.truetype(path_str, _TS_FONT_SIZE_DIALOG),
        "narrator": ImageFont.truetype(path_str, _TS_FONT_SIZE_NARRATOR),
        "sfx": ImageFont.truetype(path_str, _TS_FONT_SIZE_SFX),
    }


def _ts_load_remote_image(
    url: str | None, timeout: float = 8.0,
) -> tuple[object | None, str | None]:
    """下载远程 panel 图(Seedream / SiliconFlow / 火山方舟 临时 URL)→ (PIL Image | None, 错误类别 | None)。

    Sprint 5.x bug fix(2026-05-14):timeout 15s → 8s,返回错误类别(供页级汇总)
      根因:用户报"导出 PDF 大半天没结果",vendor 临时 URL 部分过期 / 慢响应
            15s × 72 张串行 = 18 分钟 sync block,Ctrl+C 也关不掉
      8s 容忍正常下载(SiliconFlow CDN 单图 1-2s);过期 URL 快速 fail-fast

    返回 (img, None) 表成功;(None, err_kind) 表失败 — err_kind 用于汇总分类:
      "no_url"     URL 为空
      "timeout"    超时(过期 URL / vendor 慢)
      "conn_err"   连接错(域名解析失败 / 防火墙)
      "http_err"   4xx/5xx
      "parse_err"  下载到字节但 PIL 解析失败
      "other"      其他
    """
    from PIL import Image
    if not url:
        return None, "no_url"
    try:
        import requests
        from requests.exceptions import (
            ReadTimeout, ConnectTimeout, ConnectionError as ReqConnErr, HTTPError,
        )
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
        except (ReadTimeout, ConnectTimeout):
            return None, "timeout"
        except ReqConnErr:
            return None, "conn_err"
        except HTTPError:
            return None, "http_err"
        try:
            from io import BytesIO
            return Image.open(BytesIO(resp.content)).convert("RGB"), None
        except Exception:  # noqa: BLE001
            return None, "parse_err"
    except Exception:  # noqa: BLE001
        return None, "other"


def _ts_fit_image(img, target_w: int, target_h: int):
    """object-fit: cover — resize 后裁中心区域,严格填满 (target_w, target_h)。

    源图通常 3:4(Seedream),目标 388×489 也接近 3:4,裁切量很小。
    """
    from PIL import Image
    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        return img
    src_ratio = src_w / src_h
    tgt_ratio = target_w / target_h
    if src_ratio > tgt_ratio:
        # 源更宽 → 按高 fit,左右裁
        new_h = target_h
        new_w = max(target_w, int(round(src_w * (target_h / src_h))))
    else:
        # 源更窄 → 按宽 fit,上下裁
        new_w = target_w
        new_h = max(target_h, int(round(src_h * (target_w / src_w))))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _ts_wrap_text(text: str, font, max_width: int) -> list[str]:
    """逐字符断行(中文友好;英文单词不切断处理推迟,中文场景占比 95%+)。

    返回行列表。
    """
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        test = current + char
        bbox = font.getbbox(test)
        width = bbox[2] - bbox[0]
        if width > max_width and current:
            lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _ts_draw_panel_failed(draw, x: int, y: int, panel_index: int, fonts: dict):
    """失败格占位:淡红底 + "格 N 生成失败" 文字。"""
    draw.rectangle(
        (x, y, x + _TS_PANEL_W, y + _TS_PANEL_H),
        fill=_TS_COLOR_FAILED_BG,
        outline=_TS_COLOR_PANEL_BORDER,
    )
    label = f"格 {panel_index}"
    sub = "生成失败"
    font = fonts["narrator"]
    bbox1 = font.getbbox(label)
    bbox2 = font.getbbox(sub)
    tw1, th1 = bbox1[2] - bbox1[0], bbox1[3] - bbox1[1]
    tw2, th2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
    cx = x + _TS_PANEL_W // 2
    cy = y + _TS_PANEL_H // 2
    draw.text((cx - tw1 // 2, cy - th1 - 4), label, font=font, fill=_TS_COLOR_FAILED_TEXT)
    draw.text((cx - tw2 // 2, cy + 4), sub, font=font, fill=_TS_COLOR_FAILED_TEXT)


def _ts_draw_narrator(draw, panel_x: int, panel_y: int, narrator: str, fonts: dict):
    """旁白条:顶部黄色长条(类似浮世绘 caption 风格)。"""
    font = fonts["narrator"]
    inner_w = _TS_PANEL_W - 12
    wrapped = _ts_wrap_text(narrator, font, inner_w)
    if not wrapped:
        return
    line_h = _TS_FONT_SIZE_NARRATOR + 4
    box_h = line_h * len(wrapped) + 8
    x0, y0 = panel_x + 6, panel_y + 6
    x1, y1 = x0 + inner_w, y0 + box_h
    draw.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=_TS_COLOR_NARRATOR_BG)
    for i, line in enumerate(wrapped):
        draw.text(
            (x0 + 6, y0 + 4 + i * line_h),
            line, font=font, fill=_TS_COLOR_NARRATOR_TEXT,
        )


def _ts_draw_dialogues(draw, panel_x: int, panel_y: int, dialogues: list, fonts: dict):
    """对话气泡 stack — Sprint 5.9(2026-05-14)漫画气泡 v2:

    替代 v1 的"电影字幕风(底部矩形 + 'speaker: text')",改为:
    - **气泡形状**:圆角矩形 + 黑色描边(漫画 industry-standard;椭圆 RGBA mask 复杂,
      圆角矩形视觉上已能传递"对话气泡"感)
    - **位置按 head_anchor**:left → 气泡靠左 / center → 气泡居中 / right → 气泡靠右
      (避免遮挡说话角色脸)
    - **尾巴朝上指**:从气泡顶边伸出三角形朝上,指向 panel 中上部假设的角色头位置
      (panel 388×389,角色头通常在垂直中上 1/3-1/2 区域)
    - **多句堆叠**:仍底部纵向 stack,但每句一个独立气泡 + 各自尾巴方向

    解决 Gemini 评的"图文割裂"问题 — 字幕条让画面 + 文字两层,气泡尾巴指向头让两层
    视线流连贯。
    """
    font = fonts["dialog"]
    inner_w_max = _TS_PANEL_W - 12
    line_h = _TS_FONT_SIZE_DIALOG + 4
    cur_y = panel_y + _TS_PANEL_H - 6   # 倒推 y(从底向上叠)
    TAIL_LEN = 9
    TAIL_W = 10

    for d in reversed(dialogues):
        speaker = (d.get("speaker") or "").strip()
        text = (d.get("text") or "").strip()
        if not text:
            continue
        head_anchor = (d.get("head_anchor") or "center").lower()
        if head_anchor not in ("left", "center", "right"):
            head_anchor = "center"

        full = f"{speaker}:{text}" if speaker else text
        wrapped = _ts_wrap_text(full, font, inner_w_max - 16)
        if not wrapped:
            continue
        # 气泡尺寸:max line width + padding
        max_line_w = max(
            font.getbbox(line)[2] - font.getbbox(line)[0] for line in wrapped
        )
        bubble_w = min(max_line_w + 16, inner_w_max)
        box_h = line_h * len(wrapped) + 10

        # 按 head_anchor 决定气泡水平位置(避免遮角色脸)
        # head_anchor=left → 角色在画面左,气泡放右 / 不,反过来:气泡跟着头位置走
        # 漫画惯例:气泡放在说话角色头**上方**,所以水平居于头同列
        if head_anchor == "left":
            x0 = panel_x + 6
        elif head_anchor == "right":
            x0 = panel_x + _TS_PANEL_W - bubble_w - 6
        else:  # center
            x0 = panel_x + (_TS_PANEL_W - bubble_w) // 2
        x1 = x0 + bubble_w
        y1 = cur_y
        y0 = y1 - box_h

        # 1. 画气泡:圆角矩形 + 描边
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=12,
            fill=_TS_COLOR_DIALOG_BG,
            outline=(0, 0, 0, 220),
            width=1,
        )

        # 2. 画尾巴(三角形,从气泡顶边朝上伸出指向角色头)
        # 尾巴根部 x = 气泡顶边对应 head_anchor 侧;尖端朝上偏向 head_anchor
        if head_anchor == "left":
            base_x = x0 + bubble_w // 4
            tip_x = base_x - 4   # 朝左下偏
        elif head_anchor == "right":
            base_x = x1 - bubble_w // 4
            tip_x = base_x + 4   # 朝右下偏
        else:
            base_x = (x0 + x1) // 2
            tip_x = base_x
        triangle = [
            (base_x - TAIL_W // 2, y1),    # 气泡底左
            (base_x + TAIL_W // 2, y1),    # 气泡底右
            (tip_x, y1 + TAIL_LEN),        # 尾巴尖
        ]
        draw.polygon(triangle, fill=_TS_COLOR_DIALOG_BG, outline=(0, 0, 0, 220))
        # 覆盖气泡底边一小段防裂缝(三角形与圆角矩形交界处)
        draw.line(
            [(base_x - TAIL_W // 2 + 1, y1), (base_x + TAIL_W // 2 - 1, y1)],
            fill=_TS_COLOR_DIALOG_BG,
            width=2,
        )

        # 3. 写文字(speaker 蓝色 + text 黑色)
        first_line_with_speaker = bool(speaker) and wrapped[0].startswith(speaker)
        for i, line in enumerate(wrapped):
            ty = y0 + 5 + i * line_h
            tx = x0 + 8
            if i == 0 and first_line_with_speaker:
                draw.text((tx, ty), speaker, font=font, fill=_TS_COLOR_DIALOG_SPEAKER)
                speaker_w = font.getbbox(speaker)[2] - font.getbbox(speaker)[0]
                rest = line[len(speaker):]
                draw.text((tx + speaker_w, ty), rest, font=font, fill=_TS_COLOR_DIALOG_TEXT)
            else:
                draw.text((tx, ty), line, font=font, fill=_TS_COLOR_DIALOG_TEXT)
        # 下一个气泡留尾巴空间
        cur_y = y0 - TAIL_LEN - 4


def _ts_draw_sfx(draw, panel_x: int, panel_y: int, sfx_list: list[str], fonts: dict):
    """拟声词角标:右上橙色 chip(MVP 不做旋转,留 v2 polish)。"""
    if not sfx_list:
        return
    text = " ".join(s for s in sfx_list if s).strip()
    if not text:
        return
    font = fonts["sfx"]
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad = 6
    chip_w = tw + pad * 2
    chip_h = th + pad * 2
    x1 = panel_x + _TS_PANEL_W - 6
    y0 = panel_y + 6
    x0 = x1 - chip_w
    y1 = y0 + chip_h
    draw.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=_TS_COLOR_SFX_BG)
    draw.text((x0 + pad, y0 + pad - bbox[1]), text, font=font, fill=_TS_COLOR_SFX_TEXT)


def _ts_composed_output_dir(comic_id: str) -> Path:
    """整页 PNG 输出目录:data/composed/<comic_id>/(对齐 comic_refs 布局)。"""
    base = settings.uploads_abs_dir.parent / "composed"
    return base / comic_id


def _ts_composed_url(comic_id: str, page_index: int) -> str:
    """生成对外可访问的整页 URL — 静态路由 mount 在 main.py。"""
    return f"/api/comic-composed/{comic_id}/page_{page_index}.png"


def _agent_typesetter(
    comic: Comic,
    page_index: int,
    panels_json: list[dict],
) -> str:
    """Agent #10 排版嵌字 — Python PIL 本地合成整页(Sprint 4.C 接通)。

    输入:
      comic: 元数据(取 id 作输出目录键)
      page_index: 当前页号(1-based)
      panels_json: [{panel_index, image_url, dialogues:[{speaker, text}], narrator, sfx}]
                   panel_index 缺失或乱序时按列表 index + 1 兜底

    输出:整页 composed_url(`/api/comic-composed/<comic_id>/page_<N>.png`),
          orchestrator 写入 comic_pages.composed_url 字段。

    布局规格(Sprint 4.D+ 用户实测后调整,2026-05-13):
      - 整页 800×1200 px(2:3 比例,与 reader grid 容器 aspect-ratio: 2/3 严格匹配)
      - 2 列 × 3 行 = 6 格,每格 388×389 px(≈ 1:1)
      - Seedream 生图改 "1:1" → object-fit:cover 几乎无裁切(原 3:4 panel 在 1:1
        grid 容器里被裁,用户反馈"每张图都不完整")
      - 旁白顶部黄条 / 对话底部白色气泡 stack / 拟声词右上橙色 chip
      - 失败格(image_url=None)显淡红占位 + "格 N 生成失败"

    失败兜底(soft fail):
      - 单格图下载失败:该 panel 走失败占位,继续其他格
      - 字体加载失败:抛 RuntimeError,orchestrator catch + 该 page composed_url 留 NULL
      - 文件写入失败:抛 OSError,同上
    """
    from PIL import Image, ImageDraw

    fonts = _ts_load_fonts()

    # 准备画布
    page = Image.new("RGB", (_TS_PAGE_W, _TS_PAGE_H), color=_TS_COLOR_BG)
    draw = ImageDraw.Draw(page, mode="RGBA")

    # 排序 panels(按 panel_index 升序;兜底:按列表顺序)
    panels_sorted = sorted(
        enumerate(panels_json, start=1),
        key=lambda t: int(t[1].get("panel_index", t[0])),
    )

    # Sprint 5.x bug fix(2026-05-14):**预并发下载所有 panel 图** + 页级汇总日志
    # 根因:原代码 for-loop 内串行调 _ts_load_remote_image,6 张 × 8s timeout = 48s 最坏 / 页
    # 12 页 × 48s = 9.6 分钟 sync block(用户报"导出大半天")
    # 修法:页内 6 张图用 ThreadPoolExecutor 并发,典型 1-2s / 页(瓶颈 = 最慢一张)
    import logging
    import time as _ts_time
    from urllib.parse import urlparse

    def _short_url(u: str | None) -> str:
        """URL → host + path 前 30 字(避免 log 里全是签名 token)"""
        if not u:
            return "(empty)"
        try:
            p = urlparse(u)
            return f"{p.netloc}{p.path[:30]}"
        except Exception:  # noqa: BLE001
            return u[:60]

    _ts_download_start = _ts_time.time()
    panel_imgs_by_slot: dict[int, object] = {}
    panel_errs_by_slot: dict[int, str] = {}   # slot → err_kind
    download_jobs: list[tuple[int, str | None]] = []
    for slot_idx, (_fallback_idx, panel) in enumerate(panels_sorted):
        if slot_idx >= _TS_COLS * _TS_ROWS:
            break
        download_jobs.append((slot_idx, panel.get("image_url")))

    with ThreadPoolExecutor(
        max_workers=min(6, max(len(download_jobs), 1)),
        thread_name_prefix=f"ts-p{page_index}-dl",
    ) as exe:
        future_to_slot = {
            exe.submit(_ts_load_remote_image, url): (slot, url)
            for slot, url in download_jobs
        }
        for fut in as_completed(future_to_slot):
            slot, src_url = future_to_slot[fut]
            try:
                img, err_kind = fut.result()
                if img is not None:
                    panel_imgs_by_slot[slot] = img
                else:
                    panel_imgs_by_slot[slot] = None
                    panel_errs_by_slot[slot] = err_kind or "unknown"
            except Exception as e:  # noqa: BLE001
                panel_imgs_by_slot[slot] = None
                panel_errs_by_slot[slot] = "exception"
                logging.warning(
                    f"ts page {page_index} slot {slot} {_short_url(src_url)}: "
                    f"unexpected {type(e).__name__}"
                )

    _ts_dl_elapsed = _ts_time.time() - _ts_download_start
    # 页级汇总:N/M 成功 + 错误分类
    ok_count = sum(1 for v in panel_imgs_by_slot.values() if v is not None)
    err_counts: dict[str, int] = {}
    for ek in panel_errs_by_slot.values():
        err_counts[ek] = err_counts.get(ek, 0) + 1
    if ok_count < len(download_jobs):
        err_summary = ", ".join(f"{k}={v}" for k, v in sorted(err_counts.items()))
        logging.warning(
            f"ts page {page_index}: {ok_count}/{len(download_jobs)} 下载成功,"
            f"失败分类: {err_summary} (耗时 {_ts_dl_elapsed:.1f}s, 6 并发)"
        )
    elif _ts_dl_elapsed > 3:
        logging.info(
            f"ts page {page_index} 下载 {len(download_jobs)} 张全成功,"
            f"耗时 {_ts_dl_elapsed:.1f}s (6 并发)"
        )

    for slot_idx, (fallback_idx, panel) in enumerate(panels_sorted):
        if slot_idx >= _TS_COLS * _TS_ROWS:
            break   # 超 6 格忽略(防 LLM 多产)
        panel_index = int(panel.get("panel_index", fallback_idx))
        row = slot_idx // _TS_COLS
        col = slot_idx % _TS_COLS
        x = _TS_MARGIN + col * (_TS_PANEL_W + _TS_GAP)
        y = _TS_MARGIN + row * (_TS_PANEL_H + _TS_GAP)

        # Stage 1:贴 panel 图(预下载结果)
        panel_img = panel_imgs_by_slot.get(slot_idx)
        if panel_img is None:
            _ts_draw_panel_failed(draw, x, y, panel_index, fonts)
        else:
            fitted = _ts_fit_image(panel_img, _TS_PANEL_W, _TS_PANEL_H)
            page.paste(fitted, (x, y))
            # 细边框(对齐 reader 视觉)
            draw.rectangle(
                (x, y, x + _TS_PANEL_W - 1, y + _TS_PANEL_H - 1),
                outline=_TS_COLOR_PANEL_BORDER,
                width=1,
            )

        # Stage 2:旁白条(顶部)
        narrator = panel.get("narrator")
        if isinstance(narrator, str) and narrator.strip():
            _ts_draw_narrator(draw, x, y, narrator.strip(), fonts)

        # Stage 3:对话气泡 stack(底部)
        dialogues = panel.get("dialogues") or []
        if isinstance(dialogues, list) and dialogues:
            _ts_draw_dialogues(draw, x, y, dialogues, fonts)

        # Stage 4:拟声词(右上角)
        sfx_list = panel.get("sfx") or []
        if isinstance(sfx_list, list) and sfx_list:
            _ts_draw_sfx(draw, x, y, sfx_list, fonts)

    # 写文件
    output_dir = _ts_composed_output_dir(comic.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_index}.png"
    page.save(output_path, "PNG", optimize=True)

    return _ts_composed_url(comic.id, page_index)


# ============================================================
# Sprint 4.D 导出:PDF / PNG zip(2026-05-13)
#
# 设计:
#   - 复用 Sprint 4.C typesetter 产物(data/composed/<comic_id>/page_<N>.png)
#   - PDF:PIL 多页 PDF 输出(原生支持,无新依赖)
#   - ZIP:Python stdlib zipfile,DEFLATED 压缩
#   - 缺页(composed_url=NULL 或文件丢失):跳过 + 返回 missing_pages 列表;
#     全空 → raise ExportNothingToDo(orchestrator 转 422 给前端)
#   - 不接 reader pages(panel grid 模式)— 仅 typesetter 整页;
#     用户没跑过 typesetter 的漫画暂时无法导出(symmetric:前端 UI 也只在 done 状态显按钮)
# ============================================================


class ExportNothingToDo(Exception):
    """所有 composed_url 都缺失 — 无可导出页面(orchestrator 转 422)。"""


def _ensure_page_composed(
    conn: sqlite3.Connection, comic: Comic, row
) -> Optional[Path]:
    """确保某页 composed PNG 文件存在 — Sprint 4.D+(2026-05-13)on-the-fly fallback。

    场景:
      - 老漫画(Sprint 4.C 接通前生成):composed_url=NULL,典型 missing-pages 22 撞墙
      - typesetter 偶发失败:composed_url=NULL 但 panels_json 有数据
      - 文件丢失:composed_url 有,但磁盘文件不存在(rm -rf data/composed/<comic_id>/)

    策略:文件不存在 → 实时调 _agent_typesetter(同步,无 LLM 调用,本地 PIL ≤2s/页),
          成功后写回 db `composed_url + state='composed'`,下次直接命中。
          典型 12 页全 fallback 耗时 < 30s,用户可接受。

    返回 file_path(若 ensure 成功)或 None(panels_json 也空 / 字体加载失败 / IO 错)。
    """
    page_index = int(row["page_index"])
    composed_dir = _ts_composed_output_dir(comic.id)
    file_path = composed_dir / f"page_{page_index}.png"

    # 快路径:文件已存在 + db 也已记录
    if row["composed_url"] and file_path.exists():
        return file_path

    # 慢路径:on-the-fly 跑 typesetter
    try:
        panels_json_raw = row["panels_json"]
        panels_json = json.loads(panels_json_raw) if panels_json_raw else []
        if not isinstance(panels_json, list) or not panels_json:
            # 连 panels 数据都没,放弃
            return None
    except Exception:  # noqa: BLE001
        return None

    try:
        composed_url_new = _agent_typesetter(comic, page_index, panels_json)
        # 写回 db(下次导出 / reader 直接命中)
        execute(
            conn,
            "UPDATE comic_pages SET composed_url=?, state='composed', updated_at=? "
            "WHERE comic_id=? AND page_index=?",
            (composed_url_new, _now_iso(), comic.id, page_index),
        )
        conn.commit()
        # typesetter 应该已落盘
        return file_path if file_path.exists() else None
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(
            f"export on-the-fly typesetter failed comic={comic.id} p{page_index}: "
            f"{type(e).__name__}: {e}"
        )
        return None


# Sprint 5.x bug fix(2026-05-14):export 累计超时上限
# 用户报"导出大半天没结果",根因是 on-the-fly typesetter 串行下载过期 URL × 12 页 = 18 分钟
# 修法:① typesetter 已并发(见 _agent_typesetter)② export 仍加 hard timeout,
#       超时后返 partial PDF(已生成页 + missing list)而不是死等
_EXPORT_TIMEOUT_SECONDS = 90


def _export_comic_pdf(
    conn: sqlite3.Connection, comic: Comic
) -> tuple[bytes, list[int]]:
    """整本漫画 → 多页 PDF bytes。

    返回 (pdf_bytes, missing_page_indexes)。
    missing_page_indexes 非空时,响应可加 X-Missing-Pages header 让前端 toast 提示。

    Sprint 4.D+(2026-05-13)fallback:缺页时通过 _ensure_page_composed
    on-the-fly 跑 typesetter,老漫画零迁移也能导出。

    Sprint 5.x bug fix(2026-05-14):
      - 累计 90s timeout(超时后 fail-fast,剩余页进 missing_pages,不死等)
      - 每页用时进 console log(用户能看到"在做什么",不致以为卡死)

    raises:
      ExportNothingToDo: 所有页 composed_url 缺失 + on-the-fly 也失败 → 422
    """
    from PIL import Image
    from io import BytesIO
    import time as _exp_time
    import logging as _exp_log

    rows = fetch_all(
        conn,
        "SELECT page_index, composed_url, panels_json FROM comic_pages "
        "WHERE comic_id=? ORDER BY page_index ASC",
        (comic.id,),
    )

    page_images: list = []
    missing_pages: list[int] = []
    start_time = _exp_time.time()
    timed_out = False

    _exp_log.info(f"export pdf 开始:{comic.id} 共 {len(rows)} 页,超时 {_EXPORT_TIMEOUT_SECONDS}s")

    for r in rows:
        page_index = int(r["page_index"])
        elapsed = _exp_time.time() - start_time
        if elapsed >= _EXPORT_TIMEOUT_SECONDS:
            timed_out = True
            _exp_log.warning(
                f"export pdf {comic.id} 累计耗时 {elapsed:.1f}s 超 {_EXPORT_TIMEOUT_SECONDS}s,"
                f"剩余 {len(rows) - len(page_images) - len(missing_pages)} 页进 missing"
            )
            # 剩余所有页全标 missing
            missing_pages.append(page_index)
            continue

        page_start = _exp_time.time()
        file_path = _ensure_page_composed(conn, comic, r)
        page_elapsed = _exp_time.time() - page_start
        if page_elapsed > 5:
            _exp_log.info(f"export pdf page {page_index} 耗时 {page_elapsed:.1f}s")
        if file_path is None:
            missing_pages.append(page_index)
            continue
        try:
            img = Image.open(file_path).convert("RGB")
            page_images.append(img)
        except Exception as e:  # noqa: BLE001
            _exp_log.warning(f"export pdf: 读 {file_path} 失败 {e}")
            missing_pages.append(page_index)

    if not page_images:
        raise ExportNothingToDo(
            f"comic {comic.id} 无可导出页面:所有 composed_url 缺失或文件丢失"
            + (f"(累计 {_EXPORT_TIMEOUT_SECONDS}s 超时,部分页未处理)" if timed_out else "")
        )

    total = _exp_time.time() - start_time
    _exp_log.info(
        f"export pdf {comic.id} 完工:{len(page_images)} 页 OK / {len(missing_pages)} 页 missing,"
        f"总耗时 {total:.1f}s"
    )

    buf = BytesIO()
    page_images[0].save(
        buf,
        "PDF",
        save_all=True,
        append_images=page_images[1:],
        resolution=150.0,
        title=comic.name[:60] if comic.name else "untitled-comic",
    )
    return buf.getvalue(), missing_pages


def _export_comic_zip(
    conn: sqlite3.Connection, comic: Comic
) -> tuple[bytes, list[int]]:
    """整本漫画 → ZIP bytes(每页一张 PNG)。

    返回 (zip_bytes, missing_page_indexes)。
    Sprint 4.D+ fallback + Sprint 5.x 累计超时上限,与 _export_comic_pdf 对称。
    """
    import zipfile
    from io import BytesIO
    import time as _exp_time
    import logging as _exp_log

    rows = fetch_all(
        conn,
        "SELECT page_index, composed_url, panels_json FROM comic_pages "
        "WHERE comic_id=? ORDER BY page_index ASC",
        (comic.id,),
    )

    buf = BytesIO()
    missing_pages: list[int] = []
    added = 0
    start_time = _exp_time.time()
    timed_out = False

    _exp_log.info(f"export zip 开始:{comic.id} 共 {len(rows)} 页,超时 {_EXPORT_TIMEOUT_SECONDS}s")

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            page_index = int(r["page_index"])
            elapsed = _exp_time.time() - start_time
            if elapsed >= _EXPORT_TIMEOUT_SECONDS:
                timed_out = True
                _exp_log.warning(
                    f"export zip {comic.id} 累计 {elapsed:.1f}s 超时,剩余页全标 missing"
                )
                missing_pages.append(page_index)
                continue

            page_start = _exp_time.time()
            file_path = _ensure_page_composed(conn, comic, r)
            page_elapsed = _exp_time.time() - page_start
            if page_elapsed > 5:
                _exp_log.info(f"export zip page {page_index} 耗时 {page_elapsed:.1f}s")
            if file_path is None:
                missing_pages.append(page_index)
                continue
            try:
                # arcname 用 2 位 0 填充便于解压后字典序对齐(p01 / p02 / ... / p18)
                z.write(file_path, arcname=f"page_{page_index:02d}.png")
                added += 1
            except Exception as e:  # noqa: BLE001
                _exp_log.warning(f"export zip: 加 {file_path} 失败 {e}")
                missing_pages.append(page_index)

    if added == 0:
        raise ExportNothingToDo(
            f"comic {comic.id} 无可导出页面:所有 composed_url 缺失或文件丢失"
            + (f"(累计 {_EXPORT_TIMEOUT_SECONDS}s 超时,部分页未处理)" if timed_out else "")
        )

    total = _exp_time.time() - start_time
    _exp_log.info(
        f"export zip {comic.id} 完工:{added} 页 OK / {len(missing_pages)} 页 missing,"
        f"总耗时 {total:.1f}s"
    )

    return buf.getvalue(), missing_pages


def _sanitize_filename(name: str, fallback: str = "comic") -> str:
    """对齐 OS 文件命名规则:删特殊符号 / 限长 60 字符 / 空 → fallback。"""
    if not name:
        return fallback
    # 删非法字符(win + posix 通用安全集)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    cleaned = cleaned[:60] if cleaned else fallback
    return cleaned or fallback
