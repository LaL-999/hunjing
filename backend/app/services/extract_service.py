"""自动图谱抽取服务 — Sprint 2.B + 断点续抽 + C.1 credit 重构。

主流程(threading.Thread daemon 后台跑):
  trigger_extract(upload_id, user_id) → 同步:
    1. 鉴权 + uploads 状态校验(必须 'parsed')
    2. 创建 graph_extraction_jobs 行 state='queued'
    3. is_admin_retag = (该 upload 已有 done job)→ True
    4. uploads.state → 'extracting'
    5. kick_off_extract(job_id) — 生产起线程,测试同步跑
    6. 返回 job_id

  run_extract(job_id) — 后台:
    state queued → extracting_graph → generating_characters → inferring_meta → saving → done
    任一失败 → state='failed' + error_message + uploads.state='failed'

设计(Sprint C.1):
  - **credit 在 LLM 调用完成时按真实 token 扣**(由 credit_service.consume_credits 在
    抽取完成时一次性扣 — 留 Sprint C.2 真接通);失败也不退;用户可重抽走 admin retag
  - 主角数 ≤ 8(用户拍板):PERSON 实体按 description 长度 / 在原文出现次数排序,top 8 调 character_generator
  - 其它 PERSON 实体仍入 characters 表(只有 name + identity = description)
  - 关系映射:LLM 输出非人际关系("参与"/"位于"/"拥有"/"提及")不入 relationships 表
  - EVENT 实体落 events 表;participants = build_graph relations 中"参与"类型 source/target 的人
  - 重抽:save 时按 character.name 项目内去重,skip 已存在的
  - meta 回填:projects.type / projects.custom_type_name / projects.tags

断点续抽(2.B+):
  - 每个 chunk 抽完立刻 INSERT 到 extract_chunk_results 表(per-chunk 持久化)
  - backend 重启 / 用户断网时不丢已抽部分
  - reset(用户主动放弃)→ state='failed' + upload state='parsed' + 保留 chunk_results(允许 resume)
  - resume(用户主动继续)→ state='queued' + worker 启动时从 chunk_results 加载已完成块,
                          只跑剩余的 chunk(节省 LLM 成本)
  - 重抽(走 trigger 端点)→ 全新 job_id,旧 chunk_results 保留为 orphan(归属旧 job)
  - cancel 协议:_RUNNING_EXTRACTS[job_id]["should_cancel"] = True;
                worker 在每阶段开始 + extract_graph_chunked 内部每块开始检查;
                _set_state 改条件性 UPDATE(防 worker 反向覆盖 reset 的 state='failed')
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.config import settings
from app.db import execute, fetch_all, fetch_one, get_connection, transaction
from app.models.extract_job import ExtractJob
from app.services.file_parser import parse_file
from app.services.llm_extract import (
    MINIMAL_BATCH_SIZE,
    UI_RELATION_TYPES,
    compute_chunk_hash,
    extract_graph_chunked,
    generate_character_profile,
    generate_minimal_profiles_batch,
    infer_meta,
    is_interpersonal_relation,
    normalize_relation_type,
    split_text_into_chunks,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    iso_now,
)
from app.services.quota_service import PLAN_LIMITS
from app.services.credit_service import (
    consume_credits,
    credit_units_for_text_call,
)
from app.services.upload_service import get_upload_or_404


# === 业务常量 ===
# 主角档案:精品 character_generator(每角色 ~5K token / 30s,完整 voice_fingerprint /
# behavioral_rules / key_events 等结构)— 用户拍板从 8 扩到 30(2026-05 方案 D)
TOP_CHARACTERS_FOR_PROFILE = 30
# 配角档案:批量轻量 character_minimal_profile(批 25 角色 / 1 次 LLM 调用 / ~30s,
# 输出 personality + quotes + no_go_list 简略字段)— 红楼梦 238 角色不再有半空白卡片
MAX_TAGS = 10                     # tags 软上限(防 token 爆炸)
TYPE_FALLBACK = "generic"         # AI 漏输出时的兜底


# ======================================================================
# 进程级 worker 注册表(2.B+ 断点续抽 cancel 协议)
# ======================================================================

# 模块级注册表:job_id → {"thread": Thread, "should_cancel": bool}
# - 启动 worker 时注册;worker finally 清理
# - reset 时设 should_cancel=True;worker 每阶段开始检查,看到 True → raise InterruptedError 退出
# - is_alive(job_id) 用 dict 是否含 job_id 判断(僵尸态识别:db state='extracting_*' 但
#   注册表无该 job → 必是 backend 重启后的僵尸 job,前端可显示"继续抽取")
_RUNNING_EXTRACTS: dict[str, dict] = {}
_RUNNING_LOCK = threading.Lock()


# Sprint 6.A2 FOCUS.7(2026-05-22):从 character_generator profile 提取 behavior_baseline 4 维。
# 同 refine_service 的 _SPEECH_REGISTER_VALID / _MORAL_COMPASS_VALID 严格枚举防 LLM 污染。
_BB_SPEECH_REGISTER_VALID = {"卑微", "平和", "强硬", "恶意"}
_BB_MORAL_COMPASS_VALID = {"善", "灰", "恶"}


def _extract_behavior_baseline_from_profile(profile: dict) -> Optional[dict]:
    """从 character_generator LLM 输出抽取 + 校验 behavior_baseline 3 维。

    P0G.2(2026-05-24):删 out_of_baseline_examples 字段 — 与 no_go_list 语义重复,
    且作为隐藏字段违反"透明 AI 协作"。LLM 偶然输出该字段也会被忽略丢弃。
    数据迁移见 migration 062。

    返回 normalized dict 或 None(3 维全空)。LLM 输出非法值 → 该字段设 None 不抛。

    校验:
      speech_register     必须在 _BB_SPEECH_REGISTER_VALID 内,否则置 None
      emotional_intensity 必须是 1-10 整数,否则置 None
      moral_compass       必须在 _BB_MORAL_COMPASS_VALID 内,否则置 None
    """
    raw = profile.get("behavior_baseline") if isinstance(profile, dict) else None
    if not isinstance(raw, dict):
        return None

    speech = raw.get("speech_register")
    if speech not in _BB_SPEECH_REGISTER_VALID:
        speech = None

    intensity = raw.get("emotional_intensity")
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
        intensity = None
    else:
        ivalue = int(intensity)
        intensity = ivalue if 1 <= ivalue <= 10 else None

    moral = raw.get("moral_compass")
    if moral not in _BB_MORAL_COMPASS_VALID:
        moral = None

    # 3 维全空 → 整体 None(对齐"老数据"语义,consistency_checker fallback 用 personality)
    if not speech and intensity is None and not moral:
        return None
    return {
        "speech_register": speech,
        "emotional_intensity": intensity,
        "moral_compass": moral,
    }


# Sprint 6.A2 FOCUS.5(2026-05-22):quotes 高相似度去重 — 防 LLM 抽出过度雷同的台词
# 实测痛点:《挪威的森林》"突击队"角色,LLM 抽出 12 句台词全是"地、地、地图"变种
# (突击队角色原作口吃,LLM 把所有出场台词都按原文塞进 quotes → 用户感觉很重复)
def _dedupe_similar_quotes(
    quotes: list[str], substring_min_len: int = 6,
) -> list[str]:
    """检测共享长子串去重,保留首次出现的。

    why 共享子串而非 Jaccard:
    实测 LLM 输出的口吃台词 jaccard 字符集相似度反而不高(每句有不同上下文字)。
    但它们共享显著的连续片段(如"地、地、地图""我、我嘛""我嘛")。所以改用:
      "两条 quote 共享 ≥ substring_min_len 字的连续子串 → 视为重复"

    substring_min_len=6 经验值(中文 6 字 ≈ 2-3 个词,共享 6 字以上通常是同 phrase 复制)。
    例:
      "我嘛,是学地、地、地图的。"      \ 共享 "地、地、地图" 6 字 → 后两句被去
      "嗯。大学毕业,去国土地理院,绘地、地、地图。" /
      "我不明白,我、我嘛,因为喜欢地、地、地图,才地、地、地图的。"
    反例(保留):
      "你回去吧。" / "我等你。" / "永远不会忘记。" — 任意 6 字子串都不共享
    """
    if not quotes:
        return []

    def _normalized(s: str) -> str:
        """归一化用于子串比较:去空白 / 换行,保留中文标点(顿号、句号等)。

        why 保留标点:中文口吃模式"地、地、地图" 6 字符,如果去掉顿号 → "地地地图"
        4 字符不到阈值。保留标点能精准捕获连续 phrase 重复。
        """
        import re
        return re.sub(r"\s+", "", s)

    def _has_long_common_substring(a: str, b: str, min_len: int) -> bool:
        """O(len_a * len_b) 算法找 a / b 是否存在 ≥ min_len 字符的共同连续子串。

        实现:动态规划构 LCS 长度表,但只需检测是否存在 ≥ min_len → O(len_a * len_b)。
        中文 quote 一般 < 60 字,3600 cell 足够快。
        """
        len_a, len_b = len(a), len(b)
        if len_a < min_len or len_b < min_len:
            return False
        # dp[i][j] = "以 a[i-1] 和 b[j-1] 结尾的共同后缀长度"
        # 但用 O(n) 滚动数组节省内存
        prev = [0] * (len_b + 1)
        curr = [0] * (len_b + 1)
        for i in range(1, len_a + 1):
            for j in range(1, len_b + 1):
                if a[i - 1] == b[j - 1]:
                    curr[j] = prev[j - 1] + 1
                    if curr[j] >= min_len:
                        return True
                else:
                    curr[j] = 0
            prev, curr = curr, prev
            for j in range(len_b + 1):
                curr[j] = 0
        return False

    kept: list[str] = []
    kept_normalized: list[str] = []
    for q in quotes:
        q = q.strip()
        if not q:
            continue
        q_norm = _normalized(q)
        is_dup = False
        for kn in kept_normalized:
            if _has_long_common_substring(q_norm, kn, substring_min_len):
                is_dup = True
                break
        if not is_dup:
            kept.append(q)
            kept_normalized.append(q_norm)
    return kept


def _register_worker(job_id: str, thread: threading.Thread) -> None:
    with _RUNNING_LOCK:
        _RUNNING_EXTRACTS[job_id] = {"thread": thread, "should_cancel": False}


def _unregister_worker(job_id: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING_EXTRACTS.pop(job_id, None)


def _request_cancel(job_id: str) -> bool:
    """设置 cancel 标志。返回 True 表示该 job 当前注册表里有 worker。"""
    with _RUNNING_LOCK:
        entry = _RUNNING_EXTRACTS.get(job_id)
        if entry is not None:
            entry["should_cancel"] = True
            return True
        return False


def _should_cancel(job_id: str) -> bool:
    with _RUNNING_LOCK:
        entry = _RUNNING_EXTRACTS.get(job_id)
        return bool(entry and entry["should_cancel"])


def is_job_alive(job_id: str) -> bool:
    """该 job 当前是否有活跃 worker(给 ExtractJob.is_alive 派生字段用)。

    僵尸态识别:db state='extracting_*' 且 is_job_alive=False → backend 重启后的僵尸,
    前端应显示"继续抽取/重新开始"按钮。
    """
    with _RUNNING_LOCK:
        return job_id in _RUNNING_EXTRACTS


# ======================================================================
# SSE 事件流基础设施(镜像 simulation_service Sprint 1.L 模式)
# ======================================================================

# 模块级:每个 job_id → N 个订阅者的 (queue, event_loop) 列表
EVENT_QUEUES: dict[
    str,
    list[tuple["asyncio.Queue[dict]", "asyncio.AbstractEventLoop"]],
] = {}
_QUEUES_LOCK = threading.Lock()
_QUEUE_MAXSIZE = 200
TERMINAL_EVENT_KINDS = ("done", "error")
_SSE_HEARTBEAT_SECONDS = 30.0


def _register_subscriber(
    job_id: str,
    queue: "asyncio.Queue[dict]",
    loop: "asyncio.AbstractEventLoop",
) -> None:
    with _QUEUES_LOCK:
        EVENT_QUEUES.setdefault(job_id, []).append((queue, loop))


def _unregister_subscriber(
    job_id: str, queue: "asyncio.Queue[dict]"
) -> None:
    with _QUEUES_LOCK:
        subs = EVENT_QUEUES.get(job_id, [])
        EVENT_QUEUES[job_id] = [(q, lp) for (q, lp) in subs if q is not queue]
        if not EVENT_QUEUES[job_id]:
            del EVENT_QUEUES[job_id]


def _emit(job_id: str, event: dict) -> None:
    """从 runner 工作线程跨线程推事件到所有 SSE 订阅者。

    没人订阅 → 丢弃(0 订阅者也要正常跑);put 失败 → 吞(订阅者太慢是订阅者的问题)。
    """
    with _QUEUES_LOCK:
        subs = list(EVENT_QUEUES.get(job_id, []))
    for queue, loop in subs:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except (RuntimeError, asyncio.QueueFull):
            pass


# === 异常 ===

class UploadNotExtractable(Exception):
    """upload 状态不支持抽取(非 'parsed')。"""


class ExtractJobNotResumable(Exception):
    """job 不满足 resume 条件(非 failed 状态 / 无已完成块 / 文本切片不一致)。"""


class ExtractJobNotResettable(Exception):
    """job 已是终态,无需 / 不能 reset。"""


# ======================================================================
# 取 / 鉴权工具
# ======================================================================

def get_extract_job_or_404(
    conn: sqlite3.Connection, job_id: str, user_id: str
) -> ExtractJob:
    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    if not row:
        raise ResourceNotFoundOrForbidden("extract_job", job_id)
    job = ExtractJob.from_row(row)
    if job.user_id != user_id:
        raise ResourceNotFoundOrForbidden("extract_job", job_id)
    return job


def list_project_extract_jobs(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> list[dict]:
    """列出该项目所有抽取 job(按 started_at DESC)。先鉴权 project。"""
    from app.services.project_service import get_project_or_403
    get_project_or_403(conn, project_id, user_id)
    rows = fetch_all(
        conn,
        "SELECT * FROM graph_extraction_jobs WHERE project_id=? "
        "ORDER BY started_at DESC",
        (project_id,),
    )
    return [_attach_runtime_fields(conn, ExtractJob.from_row(r)) for r in rows]


def _attach_runtime_fields(conn: sqlite3.Connection, job: ExtractJob) -> dict:
    """to_response + 派生 is_alive / resumable / completed_chunks_count。

    - is_alive: 当前 backend 进程的注册表里有 worker(假阴性可能:别 backend 实例;
                单机部署不存在;集群部署需中心化注册表 — 暂不做)
    - resumable: 有 chunks 落库 + worker 不在跑 → 用户可点继续抽取
                 2026-06-05 放宽:原本要求 state='failed',但网络卡断时 state 常停在
                 'extracting' 没人改写成 failed。改为"有 chunks + 不在跑"即可恢复。
                 done/cancelled 状态不算(那些是终态)。
    - completed_chunks_count: 用户视觉指示(已抽 N 块,剩 M 块)
    """
    base = job.to_response()
    base["is_alive"] = is_job_alive(job.id)
    base["completed_chunks_count"] = _count_completed_chunks(conn, job.id)
    base["resumable"] = (
        base["completed_chunks_count"] > 0
        and not base["is_alive"]
        and job.state in ("failed", "extracting", "queued")
    )
    return base


# ======================================================================
# trigger — 同步:配额 + 创建 job + 起线程
# ======================================================================

def trigger_extract(
    conn: sqlite3.Connection, upload_id: str, user_id: str, plan: str
) -> dict:
    """同步触发,返回 ExtractJobResponse 形 dict。

    支持的 upload 状态:
      - 'parsed'    首次抽取的正常路径
      - 'ready'     **重抽路径(Sprint 6.A2 polish,2026-05-18)** —
                    清旧 chunk_results / 旧 done job 标记 superseded /
                    upload state 重置为 'extracting' / 新 job 走 admin_retag
                    用户场景:prompt 升级了 / 想换 build_graph 版本 / 上次抽得不好
                    (对齐 CLAUDE.md 教训 #15"prompt 升级后用户需主动重抽")
      - 'extracting' 仍在抽 → 拒(防并发触发同一 upload 多个 job)
      - 其他状态     拒

    异常:
      ResourceNotFoundOrForbidden  upload 不属于该用户
      UploadNotExtractable         upload.state 不在白名单

    Sprint C.1(2026-05-13):enforce_extract_quota 已删除 — credit 由抽取完成时
    consume_credits(action='extract', units=按真实 token) 扣。
    """
    upload = get_upload_or_404(conn, upload_id, user_id)
    if upload.state == "extracting":
        raise UploadNotExtractable(
            "upload 正在抽取中,请等待当前任务完成或先 reset"
        )
    if upload.state not in ("parsed", "ready"):
        raise UploadNotExtractable(
            f"upload 状态需为 'parsed' 或 'ready'(当前:{upload.state})"
        )

    # 检查是否重抽(同 upload 之前已有 done job)
    prior_done = fetch_one(
        conn,
        "SELECT id FROM graph_extraction_jobs "
        "WHERE upload_id=? AND state='done' LIMIT 1",
        (upload_id,),
    )
    is_admin_retag = prior_done is not None

    # Sprint 6.A2 polish(2026-05-18):重抽时清理旧数据
    # - 删旧 extract_chunk_results(让 protagonist_judger / scene_extractor /
    #   character_affinity 扫到的是新版数据,不混杂旧版本 LLM 输出)
    # - characters / relationships / events 主表保留(用户改过的内容不动;
    #   _extract_chunks_loop 合并旧主表 + 新抽实体 → 不删既有)
    # - 旧 done job 留作审计(state 仍 'done')— 不强制改 'superseded'(YAGNI)
    is_re_extract = upload.state == "ready"
    if is_re_extract:
        execute(
            conn,
            """DELETE FROM extract_chunk_results
               WHERE job_id IN (
                   SELECT id FROM graph_extraction_jobs WHERE upload_id=?
               )""",
            (upload_id,),
        )

    # 创建 job 行
    job_id = str(uuid.uuid4())
    started_at = iso_now()
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO graph_extraction_jobs "
            "(id, upload_id, user_id, project_id, state, is_admin_retag, started_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (
                job_id, upload_id, user_id, upload.project_id,
                1 if is_admin_retag else 0, started_at,
            ),
        )
        # uploads.state → 'extracting'(占位:防止重复 trigger)
        # 'ready' → 'extracting' 也走此路径(SQLite CHECK 约束允许此过渡)
        execute(
            tx,
            "UPDATE uploads SET state='extracting' WHERE id=?", (upload_id,),
        )

    # Sprint C.1:credit 计费在抽取完成时由 _extract_chunks_loop 内部触发
    # consume_credits(action="extract", units=按真实 token,related_id=job_id)

    # 起后台
    kick_off_extract(job_id)

    # 返回最新 job(派生 runtime 字段)
    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    return _attach_runtime_fields(conn, ExtractJob.from_row(row))


# Sprint C.1(2026-05-13):credit 模式 — 抽取完成时由 _extract_chunks_loop 内部
# 调 credit_service.consume_credits(action='extract', units=按真实 token) 扣 credit。
# 完成抽取时 extract_service 内部 UPDATE graph_extraction_jobs.cost_yuan 仍记录真实 ¥成本。


# ======================================================================
# reset / resume(2.B+)
# ======================================================================

def reset_extract_job(
    conn: sqlite3.Connection, job_id: str, user_id: str
) -> dict:
    """用户主动放弃当前 job(僵尸态 / 不想等 / 想换文件)。

    - 设 worker cancel 标志(若进程内还活着)
    - DB:job state='failed' + upload state='parsed'
    - **保留 chunk_results**:允许后续 resume(同 job_id 继续)
    - 不退配额(LLM 已经付了,退不回来)

    Raises:
      ResourceNotFoundOrForbidden  job 不存在或不属于用户
      ExtractJobNotResettable      job 已 done/failed(已是终态,无需 reset)
    """
    job = get_extract_job_or_404(conn, job_id, user_id)
    if job.state in ("done", "failed"):
        raise ExtractJobNotResettable(
            f"job 已是终态({job.state}),无需 reset"
        )

    # 1. 进程内 cancel(若 worker 还活着)
    _request_cancel(job_id)

    # 2. DB 翻状态 — 用条件 UPDATE 防 race(extremely unlikely 但稳妥):
    #    - 只在 state 仍 extracting_* 时改;若 worker 抢先到 done/failed,reset 自动 noop
    completed_at = iso_now()
    err_msg = "用户主动重置(可点继续抽取从断点恢复)"
    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE graph_extraction_jobs SET state='failed', "
            "error_message=?, completed_at=? "
            "WHERE id=? AND state NOT IN ('done', 'failed')",
            (err_msg, completed_at, job_id),
        )
        # upload 翻回 parsed(允许重新触发或 resume)
        execute(
            tx, "UPDATE uploads SET state='parsed', error_message=NULL WHERE id=?",
            (job.upload_id,),
        )

    # 3. SSE 推 error 事件让活跃订阅者知道(若有)
    _emit(job_id, {"kind": "error", "message": err_msg})

    # 返回更新后的 job
    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    return _attach_runtime_fields(conn, ExtractJob.from_row(row))


def resume_extract_job(
    conn: sqlite3.Connection, job_id: str, user_id: str
) -> dict:
    """用户主动继续(从断点恢复)。

    前置条件:
      - job state='failed'(reset 之后 / worker 挂了被人为标 failed)
      - chunk_results 有数据(否则等于全新抽取,应走 trigger)
      - upload 仍存在且文件可读

    流程:
      1. 校验前置条件
      2. DB:job state='queued' + 清 error_message + completed_at + upload state='extracting'
      3. kick_off_extract(同 job_id 继续)— worker 内会 _load_completed_chunks 跳过已完成块
      4. **不扣配额**(同一 job 的延续)

    Raises:
      ResourceNotFoundOrForbidden  job 不存在或不属于用户
      ExtractJobNotResumable       job 状态非 failed / 无已完成块
    """
    job = get_extract_job_or_404(conn, job_id, user_id)
    # 2026-06-05 放宽:原本只允许 state='failed' resume,但 reset 后 race 或 worker
    # 异常退出可能让 state 卡在 extracting/queued。只要有 chunks + worker 不在跑就放行。
    if job.state == "done":
        raise ExtractJobNotResumable(
            "job 已完成,无需 resume"
        )
    if is_job_alive(job_id):
        raise ExtractJobNotResumable(
            f"job 当前还在抽取中(worker 活着),不能并发 resume"
        )
    completed_count = _count_completed_chunks(conn, job_id)
    if completed_count == 0:
        raise ExtractJobNotResumable(
            "无已完成块,resume 等于全新抽取 — 请走重新抽取(扣新配额)"
        )

    # upload 必须可用(不能被删了)
    upload = get_upload_or_404(conn, job.upload_id, user_id)

    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE graph_extraction_jobs SET state='queued', "
            "error_message=NULL, completed_at=NULL WHERE id=?",
            (job_id,),
        )
        execute(
            tx,
            "UPDATE uploads SET state='extracting', error_message=NULL WHERE id=?",
            (upload.id,),
        )

    kick_off_extract(job_id)

    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    return _attach_runtime_fields(conn, ExtractJob.from_row(row))


# ======================================================================
# Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核 — approve_entities
# ======================================================================

class ExtractJobNotInReview(Exception):
    """job 当前不在 entities_pending_review 状态,不能 approve。"""


def approve_entities(
    conn: sqlite3.Connection,
    job_id: str,
    user_id: str,
    added_persons: list[dict],   # 用户手动 + 的 PERSON,字段:{name, description?}
    removed_names: list[str],    # 用户标记移除的 PERSON name
) -> dict:
    """用户审核完 entities,更新 extracted_graph_json + 切 state 进入档案生成阶段。

    流程:
      1. 校验 job 在 entities_pending_review 状态
      2. 拿 extracted_graph_json,过滤掉 removed_names 对应的 PERSON,加上 added_persons
      3. 也清理 relations 里指向 removed_names 的边(防孤儿)
      4. 写回 extracted_graph_json + state='generating_characters'
      5. kick_off_extract(job_id)— worker 重启,走 fast-path 直接进 profile 阶段

    Raises:
      ResourceNotFoundOrForbidden  job 不存在或不属于用户
      ExtractJobNotInReview        job 状态非 entities_pending_review
    """
    job = get_extract_job_or_404(conn, job_id, user_id)
    if job.state != "entities_pending_review":
        raise ExtractJobNotInReview(
            f"job 当前状态 {job.state},只有 entities_pending_review 状态可批准 entities"
        )
    if not job.extracted_graph_json:
        raise ExtractJobNotInReview("job.extracted_graph_json 为空,不能批准")

    graph_data = json.loads(job.extracted_graph_json)
    entities = list(graph_data.get("entities", []) or [])
    relations = list(graph_data.get("relations", []) or [])

    # 1. 过滤掉 removed_names 的 PERSON
    removed_set = set(removed_names or [])
    if removed_set:
        entities = [
            e for e in entities
            if not (
                isinstance(e, dict)
                and e.get("type") == "PERSON"
                and e.get("name") in removed_set
            )
        ]
        # 关系也清:source/target 不能再指向被删的 name
        relations = [
            r for r in relations
            if isinstance(r, dict)
            and r.get("source") not in removed_set
            and r.get("target") not in removed_set
        ]

    # 2. 加上用户手动 + 的 PERSON(name 必填,description 可空,aliases 默认空)
    existing_names = {
        e.get("name") for e in entities
        if isinstance(e, dict) and e.get("type") == "PERSON"
    }
    for added in added_persons or []:
        if not isinstance(added, dict):
            continue
        name = (added.get("name") or "").strip()
        if not name or name in existing_names:
            continue
        entities.append({
            "name": name,
            "type": "PERSON",
            "aliases": [],
            "description": (added.get("description") or "").strip(),
        })
        existing_names.add(name)

    graph_data["entities"] = entities
    graph_data["relations"] = relations

    # 3. 落库 + 切状态
    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE graph_extraction_jobs "
            "SET extracted_graph_json=?, state='generating_characters' "
            "WHERE id=? AND state='entities_pending_review'",
            (json.dumps(graph_data, ensure_ascii=False), job_id),
        )

    # 4. 启动 worker 续跑(fast-path 命中:跳过 graph 阶段直接生成档案)
    kick_off_extract(job_id)

    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    return _attach_runtime_fields(conn, ExtractJob.from_row(row))


# ======================================================================
# chunk_results 持久化(2.B+ 断点续抽核心)
# ======================================================================

def _save_chunk_result(
    conn: sqlite3.Connection,
    job_id: str,
    chunk_index: int,
    chunk_text_hash: str,
    graph_data: dict,
    tokens_input: int,
    tokens_output: int,
    chunk_text: str = "",
) -> None:
    """单块抽取完成 → 立刻持久化。

    INSERT OR REPLACE 防 worker 重试场景下同 (job_id, chunk_index) 重复插入。

    Sprint 6.A2 M3.C(2026-05-18):chunk_text 字段(migration 039)用于灵魂续写 RAG 召回。
    新抽时直接落原文;老数据无 chunk_text,RAG 时 lazy backfill。
    """
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT OR REPLACE INTO extract_chunk_results "
            "(job_id, chunk_index, chunk_text_hash, graph_json, "
            " tokens_input, tokens_output, completed_at, chunk_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, chunk_index, chunk_text_hash,
                json.dumps(graph_data, ensure_ascii=False),
                tokens_input, tokens_output, iso_now(),
                chunk_text or None,
            ),
        )


def _load_completed_chunks(
    conn: sqlite3.Connection, job_id: str
) -> dict[int, tuple[str, dict, int, int]]:
    """加载已完成块映射:{chunk_index_1based: (hash, graph_data, in_tokens, out_tokens)}

    extract_graph_chunked 用它判断 chunk N 是否已抽过(hash 一致才跳过)。
    """
    rows = fetch_all(
        conn,
        "SELECT chunk_index, chunk_text_hash, graph_json, tokens_input, tokens_output "
        "FROM extract_chunk_results WHERE job_id=? ORDER BY chunk_index",
        (job_id,),
    )
    out: dict[int, tuple[str, dict, int, int]] = {}
    for r in rows:
        try:
            graph = json.loads(r["graph_json"])
        except (json.JSONDecodeError, TypeError):
            continue   # 损坏行,跳过(等于该 chunk 重抽)
        out[int(r["chunk_index"])] = (
            str(r["chunk_text_hash"]),
            graph if isinstance(graph, dict) else {},
            int(r["tokens_input"]),
            int(r["tokens_output"]),
        )
    return out


def _count_completed_chunks(conn: sqlite3.Connection, job_id: str) -> int:
    """已完成块数(给 resumable 派生用)。"""
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS n FROM extract_chunk_results WHERE job_id=?",
        (job_id,),
    )
    return int(row["n"]) if row else 0


def _sum_completed_chunks_tokens(
    conn: sqlite3.Connection, job_id: str
) -> tuple[int, int]:
    """已完成块的累计 token(resume 时 worker 起步基线)。"""
    row = fetch_one(
        conn,
        "SELECT COALESCE(SUM(tokens_input), 0) AS in_t, "
        "       COALESCE(SUM(tokens_output), 0) AS out_t "
        "FROM extract_chunk_results WHERE job_id=?",
        (job_id,),
    )
    if not row:
        return 0, 0
    return int(row["in_t"]), int(row["out_t"])


# ======================================================================
# 后台 worker — kick_off + run_extract 4 阶段
# ======================================================================

def _default_kick_off_extract(job_id: str) -> None:
    """生产:起后台 daemon 线程跑 run_extract,trigger 立即返回。

    与 simulation_service._default_kick_off 同模式 — FastAPI sync route 在
    starlette threadpool 跑,worker 线程没有 event loop,用纯线程最干净。
    2026-06-05 BYOK:capture context 让 thread 内 LLM 调用拿到用户 user_id。
    """
    from app.services.byok_context import capture_current_context
    ctx = capture_current_context()
    thread = threading.Thread(
        target=ctx.run,
        args=(run_extract, job_id),
        daemon=True,
        name=f"extract-{job_id[:8]}",
    )
    _register_worker(job_id, thread)
    thread.start()


# 模块级可替换(测试 monkeypatch 此变量为 run_extract 直接同步跑)
kick_off_extract: Callable[[str], None] = _default_kick_off_extract


def run_extract(job_id: str) -> None:
    """同步执行整个抽取 — 生产由 _default_kick_off_extract 在线程跑,测试直接调。

    每阶段:
      _set_state(job_id, 'extracting_graph') → _do_extract_graph → 落 extracted_graph_json
      _set_state(job_id, 'generating_characters') → _do_generate_characters
      _set_state(job_id, 'inferring_meta') → _do_infer_meta → 回填 projects
      _set_state(job_id, 'saving') → _do_save_to_project
      _set_state(job_id, 'done') + uploads.state → 'ready'

    任一阶段抛异常 → _mark_failed(job_id, str(e))。
    InterruptedError(用户 cancel)→ 安静退出,不调 _mark_failed(reset 已经改了 state)。
    """
    conn = get_connection()
    try:
        job = _fetch_job(conn, job_id)
        if not job:
            return
        upload = get_upload_or_404(conn, job.upload_id, job.user_id)
        from app.services.project_service import get_project_or_403
        project = get_project_or_403(conn, job.project_id, job.user_id)

        # 读全文
        abs_path = settings.uploads_abs_dir / upload.storage_path
        parse_result = parse_file(abs_path, upload.mime_type)
        if not parse_result.success:
            _mark_failed(conn, job_id, f"读上传文件失败:{parse_result.error}")
            return
        text = parse_result.text

        # === 0. 加载断点续抽数据(if any)===
        # resume 入口:job 之前抽过几块,从 extract_chunk_results 拉历史
        completed_chunks = _load_completed_chunks(conn, job_id)
        # resume 模式下,token 累计基线 = 已完成块的 token 总和
        baseline_in, baseline_out = _sum_completed_chunks_tokens(conn, job_id)
        is_resume = bool(completed_chunks)
        if is_resume:
            import logging
            logging.info(
                "extract job %s resume — 已完成 %d 块,跳过这些 chunk",
                job_id, len(completed_chunks),
            )

        total_in_tokens = baseline_in
        total_out_tokens = baseline_out

        # === FOCUS.10(2026-05-22)人机协同审核 — resume fast-path ===
        # 用户审核已批准 → state='generating_characters' + extracted_graph_json 已被
        # approve_entities 端点更新成用户审定的版本 → 跳过整个 graph 阶段,直接用用户
        # 审核后的 entities/relations 进 profile 阶段(避免 _merge_graphs 又把用户改的覆盖)
        resume_from_review = (
            job.state == "generating_characters"
            and job.extracted_graph_json
        )
        graph_data: Optional[dict] = None
        if resume_from_review:
            graph_data = json.loads(job.extracted_graph_json)
            total_in_tokens = job.tokens_input or 0
            total_out_tokens = job.tokens_output or 0
            import logging
            logging.info(
                "extract job %s resume from review — 跳过 graph 阶段,直接进 profile",
                job_id,
            )

        if graph_data is None:
            # === 1. 抽 graph(长文自动分块 + 合并去重 + 断点续抽)===
            _set_state(conn, job_id, "extracting_graph")
            if _should_cancel(job_id):
                raise InterruptedError("用户取消(extracting_graph 入口)")

            from app.services.llm_extract import DEFAULT_CHUNK_CHARS
            est_total_chunks = max(1, (len(text) + DEFAULT_CHUNK_CHARS - 1) // DEFAULT_CHUNK_CHARS)
            _emit(job_id, {
                "kind": "extract_graph_start",
                "total_chunks": est_total_chunks,
                "total_chars": len(text),
                "resumed_chunks": len(completed_chunks),   # 给前端显"已恢复 N 块"
            })

            def _on_chunk_done(
                done: int, total: int, partial_usage: dict,
                chunk_hash: str, chunk_graph: dict, chunk_text: str = "",
            ) -> None:
                # 1. 持久化该块(断点续抽核心 — 这一刻断电也不丢)
                #    Sprint 6.A2 M3.C(2026-05-18):chunk_text 落库给 RAG 召回用
                _save_chunk_result(
                    conn, job_id, done, chunk_hash, chunk_graph,
                    tokens_input=partial_usage["input_tokens"],
                    tokens_output=partial_usage["output_tokens"],
                    chunk_text=chunk_text,
                )
                # 2. job 行的 tokens_input/output 累加(轮询兜底也能看到)
                cur_total_in = baseline_in + partial_usage["input_tokens"]
                cur_total_out = baseline_out + partial_usage["output_tokens"]
                _save_intermediate(
                    conn, job_id,
                    tokens_input=cur_total_in,
                    tokens_output=cur_total_out,
                )
                # 3. SSE:实时推每块完成
                # FOCUS.9(2026-05-22):带本块抽出的 entities/relations 数,前端显"段内抽了 X 角色 Y 关系"
                chunk_ents = chunk_graph.get("entities", []) if isinstance(chunk_graph, dict) else []
                chunk_rels = chunk_graph.get("relations", []) if isinstance(chunk_graph, dict) else []
                chunk_persons = [
                    e for e in chunk_ents
                    if isinstance(e, dict) and e.get("type") == "PERSON"
                ]
                _emit(job_id, {
                    "kind": "chunk_done",
                    "chunk_index": done,
                    "total_chunks": total,
                    "tokens_input": cur_total_in,
                    "tokens_output": cur_total_out,
                    # 本块统计(用户视角:看到"这段抽出了 X 人 Y 关系")
                    "chunk_persons_count": len(chunk_persons),
                    "chunk_entities_count": len(chunk_ents),
                    "chunk_relations_count": len(chunk_rels),
                })

            def _on_chunk_failed(failed: int, total: int, err_msg: str) -> None:
                _emit(job_id, {
                    "kind": "chunk_failed",
                    "chunk_index": failed,
                    "total_chunks": total,
                    "message": err_msg,
                })

            def _on_chunk_skipped(skipped_idx: int, total: int) -> None:
                # resume 命中已完成块 — 让前端显"第 N 块已恢复(免抽)"
                _emit(job_id, {
                    "kind": "chunk_skipped",
                    "chunk_index": skipped_idx,
                    "total_chunks": total,
                })

            graph_data, usage = extract_graph_chunked(
                text, work_name=project.name, work_type="作品",
                on_chunk_done=_on_chunk_done,
                on_chunk_failed=_on_chunk_failed,
                on_chunk_skipped=_on_chunk_skipped,
                completed_chunks=completed_chunks,
                should_cancel=lambda: _should_cancel(job_id),
            )
            # usage 只算本次新抽的 token(extract_graph_chunked 不重复计 resume 块)
            total_in_tokens = baseline_in + usage.get("input_tokens", 0)
            total_out_tokens = baseline_out + usage.get("output_tokens", 0)

            # === Sprint 6.A2 FOCUS.12(2026-05-22):LLM 实体消解 ===
            # 治 Gemini 实测痛点 — _post_merge_alias_dedup 程序级合并无法识别"行男 = 病人"
            # / "客栈掌柜 = 客栈主人"等零字符重叠的等价称呼。在程序合并后再跑 LLM 校验。
            try:
                from app.services.llm_extract import (
                    dedup_persons_with_llm,
                    _apply_llm_dedup_to_entities,
                )
                # 只对 PERSON 跑(LOCATION/EVENT/OBJECT 不需要)
                person_entities = [
                    e for e in graph_data.get("entities", [])
                    if isinstance(e, dict) and e.get("type") == "PERSON"
                ]
                merge_groups, dedup_usage = dedup_persons_with_llm(
                    person_entities, full_text=text,
                )
                if merge_groups:
                    # 重建 entities_by_name(只含 PERSON,LOCATION 等保持原样)
                    person_by_name = {
                        p.get("name"): p for p in person_entities if p.get("name")
                    }
                    non_persons = [
                        e for e in graph_data.get("entities", [])
                        if isinstance(e, dict) and e.get("type") != "PERSON"
                    ]
                    new_persons, new_relations, name_remap = _apply_llm_dedup_to_entities(
                        person_by_name,
                        merge_groups,
                        graph_data.get("relations", []),
                    )
                    graph_data["entities"] = list(new_persons.values()) + non_persons
                    graph_data["relations"] = new_relations
                    import logging
                    logging.info(
                        "LLM dedup job %s 合并 %d 组(remap %d names)",
                        job_id, len(merge_groups), len(name_remap),
                    )
                # 消解 LLM 的 token 也累加到 job 总计
                total_in_tokens += dedup_usage.get("input_tokens", 0)
                total_out_tokens += dedup_usage.get("output_tokens", 0)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning("LLM 实体消解失败,跳过(不阻塞主流程): %s", e)

            _save_intermediate(
                conn, job_id, extracted_graph_json=json.dumps(graph_data, ensure_ascii=False),
                tokens_input=total_in_tokens, tokens_output=total_out_tokens,
            )
            # FOCUS.9(2026-05-22):漏抽人名检测 — 扫描 PERSON description 反向找未列出的角色
            try:
                from app.services.llm_extract import detect_missed_persons as _detect_missed
                merged_entities_dict = {
                    e.get("name", ""): e
                    for e in graph_data.get("entities", [])
                    if isinstance(e, dict) and e.get("name")
                }
                missed_persons = _detect_missed(merged_entities_dict, text)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning("detect_missed_persons failed: %s", e)
                missed_persons = []

            _emit(job_id, {
                "kind": "extract_graph_done",
                "entities_count": len(graph_data.get("entities", [])),
                "relations_count": len(graph_data.get("relations", [])),
                "missed_persons": missed_persons,
            })

            # === FOCUS.10(2026-05-22)人机协同审核阶段 ===
            # 提取已完成 → 暂停 worker 等用户审 entities(增删 PERSON)
            # 用户在前端 modal 改完后,POST /jobs/:id/approve_entities → kick_off_extract 续跑
            persons_for_review = [
                {
                    "name": e.get("name", ""),
                    "description": e.get("description", ""),
                    "aliases": e.get("aliases", []),
                    # P0M.4(2026-05-24)— text_occurrences 加 aliases 求和
                    # 治"叶子弟弟 / 面食店女人"显示出场 0 次:
                    # name 是 LLM 概括的规范名(原文里实际叫"佐一郎"/"我弟弟"),
                    # 单算 name.count 永远为 0;加 aliases 求和才反映真实出场频次
                    "text_occurrences": (
                        (text.count(e.get("name", "")) if e.get("name") else 0)
                        + sum(
                            text.count(a)
                            for a in (e.get("aliases") or [])
                            if isinstance(a, str) and a.strip()
                        )
                    ),
                }
                for e in graph_data.get("entities", [])
                if isinstance(e, dict) and e.get("type") == "PERSON"
            ]
            _set_state(conn, job_id, "entities_pending_review")
            _emit(job_id, {
                "kind": "entities_pending_review",
                "persons": persons_for_review,
                "missed_persons": missed_persons,
            })
            # worker 早退 — 等用户审核;不调 _mark_failed(state 已是 entities_pending_review)
            import logging
            logging.info(
                "extract job %s 暂停在 entities_pending_review,等用户审核",
                job_id,
            )
            return

        # === 2. 逐角色档案(top N PERSON)===
        # 进入这里说明用户已批准(state='generating_characters'),或 resume 从档案阶段恢复
        if _should_cancel(job_id):
            raise InterruptedError("用户取消(generating_characters 入口)")
        # state 已经在 approve_entities 端点里设过,这里幂等 set 一次(防 race)
        if job.state != "generating_characters":
            _set_state(conn, job_id, "generating_characters")

        person_entities = [
            e for e in graph_data.get("entities", [])
            if isinstance(e, dict) and e.get("type") == "PERSON"
        ]
        # 简单按出现频次排序 — name + aliases 在原文中出现次数总和
        def freq(ent: dict) -> int:
            keys = [ent.get("name", "")] + list(ent.get("aliases") or [])
            return sum(text.count(k) for k in keys if k)
        person_entities.sort(key=freq, reverse=True)
        top_persons = person_entities[:TOP_CHARACTERS_FOR_PROFILE]

        _emit(job_id, {
            "kind": "characters_start",
            "total_profiles": len(top_persons),
            "names": [p.get("name", "") for p in top_persons],
        })

        profiles_by_name: dict[str, dict] = {}   # name → 完整 profile
        for idx, ent in enumerate(top_persons):
            if _should_cancel(job_id):
                raise InterruptedError("用户取消(profile 阶段)")
            char_name = ent.get("name", "")
            _emit(job_id, {
                "kind": "profile_start",
                "profile_index": idx + 1,
                "total_profiles": len(top_persons),
                "character_name": char_name,
            })
            try:
                profile, p_usage = generate_character_profile(
                    work_name=project.name,
                    language_style="通用",   # v1 不细分;后续可由 infer_meta 输出语体
                    char_entity=ent,
                    source_text=text,
                )
                profiles_by_name[char_name] = profile
                total_in_tokens += p_usage.get("input_tokens", 0)
                total_out_tokens += p_usage.get("output_tokens", 0)
                _emit(job_id, {
                    "kind": "profile_done",
                    "profile_index": idx + 1,
                    "total_profiles": len(top_persons),
                    "character_name": char_name,
                    "tokens_input": total_in_tokens,
                    "tokens_output": total_out_tokens,
                })
            except Exception as e:  # noqa: BLE001
                # 单角色失败不阻塞:用 entity 信息做降级 profile
                import logging
                logging.warning("生成 %s 档案失败,降级用 entity: %s", char_name, e)
                _emit(job_id, {
                    "kind": "profile_failed",
                    "profile_index": idx + 1,
                    "total_profiles": len(top_persons),
                    "character_name": char_name,
                })
        _save_intermediate(
            conn, job_id,
            tokens_input=total_in_tokens, tokens_output=total_out_tokens,
        )

        # === 2.b 批量轻量档案补全(top 30 之外的配角,2026-05 方案 D)===
        # 红楼梦 238 角色 - top 30 = 208 配角 / 25 = 9 批 / 4.5 分钟,~0.05 元成本
        # 让所有角色卡片不再是"半空白":配角也有 personality / quotes / no_go(简略版)
        minimal_profiles_by_name: dict[str, dict] = {}
        top_person_names_set = {ent.get("name", "") for ent in top_persons}
        remaining_persons = [
            ent for ent in person_entities
            if ent.get("name", "") not in top_person_names_set
        ]
        if remaining_persons:
            if _should_cancel(job_id):
                raise InterruptedError("用户取消(批量补全 入口)")
            total_batches = (len(remaining_persons) + MINIMAL_BATCH_SIZE - 1) // MINIMAL_BATCH_SIZE
            _emit(job_id, {
                "kind": "minimal_batch_start",
                "total_remaining_persons": len(remaining_persons),
                "total_batches": total_batches,
            })

            language_style_for_minimal = "通用"   # v1 与 top 30 对齐;后续可由 infer_meta 细分

            for batch_idx in range(total_batches):
                if _should_cancel(job_id):
                    raise InterruptedError("用户取消(批量补全 中段)")
                batch_1based = batch_idx + 1
                start = batch_idx * MINIMAL_BATCH_SIZE
                batch = remaining_persons[start : start + MINIMAL_BATCH_SIZE]
                try:
                    batch_profiles, b_usage = generate_minimal_profiles_batch(
                        work_name=project.name,
                        language_style=language_style_for_minimal,
                        person_entities=batch,
                        source_text=text,
                    )
                    for prof in batch_profiles:
                        name = prof.get("name")
                        if name:
                            minimal_profiles_by_name[name] = prof
                    total_in_tokens += b_usage.get("input_tokens", 0)
                    total_out_tokens += b_usage.get("output_tokens", 0)
                    _emit(job_id, {
                        "kind": "minimal_batch_done",
                        "batch_index": batch_1based,
                        "total_batches": total_batches,
                        "persons_filled": len(batch_profiles),
                        "tokens_input": total_in_tokens,
                        "tokens_output": total_out_tokens,
                    })
                except Exception as e:  # noqa: BLE001
                    # 单批失败不阻塞:这批角色降级为 identity-only
                    import logging
                    logging.warning(
                        "批量补全 %d/%d 失败,该批 %d 配角降级 identity-only: %s",
                        batch_1based, total_batches, len(batch), e,
                    )
                    _emit(job_id, {
                        "kind": "minimal_batch_failed",
                        "batch_index": batch_1based,
                        "total_batches": total_batches,
                        "persons_count": len(batch),
                        "message": f"{type(e).__name__}: {e}"[:200],
                    })

            _save_intermediate(
                conn, job_id,
                tokens_input=total_in_tokens, tokens_output=total_out_tokens,
            )

        # === 3. 推 meta(type / custom_type_name / tags)===
        if _should_cancel(job_id):
            raise InterruptedError("用户取消(inferring_meta 入口)")
        _set_state(conn, job_id, "inferring_meta")
        try:
            meta, m_usage = infer_meta(project.name, text)
            total_in_tokens += m_usage.get("input_tokens", 0)
            total_out_tokens += m_usage.get("output_tokens", 0)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.warning("infer_meta 失败,用兜底值: %s", e)
            meta = {"type": TYPE_FALLBACK, "custom_type_name": None, "tags": []}

        inferred_type = meta.get("type") if isinstance(meta.get("type"), str) else TYPE_FALLBACK
        if inferred_type not in ("novel", "comic", "anime", "generic"):
            # AI 输出非 4 类(如直接给"剧本杀")→ 走 generic + custom_type_name 兜底
            inferred_custom = inferred_type
            inferred_type = "generic"
        else:
            inferred_custom = meta.get("custom_type_name")
            if inferred_custom is not None and not isinstance(inferred_custom, str):
                inferred_custom = None
        raw_tags = meta.get("tags") or []
        if not isinstance(raw_tags, list):
            raw_tags = []
        cleaned_tags = [str(t).strip() for t in raw_tags if str(t).strip()][:MAX_TAGS]

        # 2.C+ polish: world_baseline(infer_meta v2 输出 6 字段)
        # llm_extract.infer_meta 已经做了清洗 + "未识别"兜底,这里直接拿
        world_baseline = meta.get("world_baseline") if isinstance(meta.get("world_baseline"), dict) else {}

        _save_intermediate(
            conn, job_id,
            inferred_type=inferred_type,
            inferred_custom_type_name=inferred_custom,
            inferred_tags_json=json.dumps(cleaned_tags, ensure_ascii=False),
            tokens_input=total_in_tokens, tokens_output=total_out_tokens,
        )
        _emit(job_id, {
            "kind": "meta_inferred",
            "inferred_type": inferred_type,
            "inferred_custom_type_name": inferred_custom,
            "inferred_tags": cleaned_tags,
        })

        # === 4. batch save 到项目 ===
        if _should_cancel(job_id):
            raise InterruptedError("用户取消(saving 入口)")
        _set_state(conn, job_id, "saving")
        counts = _save_to_project(
            conn,
            project_id=job.project_id,
            entities=graph_data.get("entities", []),
            relations=graph_data.get("relations", []),
            profiles_by_name=profiles_by_name,
            minimal_profiles_by_name=minimal_profiles_by_name,
            top_persons=top_persons,
        )
        # 回填 projects.type / custom_type_name / tags / world_baseline_json(2.C+ polish)
        # Sprint 6.A2 FOCUS.2(2026-05-21):同步回填 narrative_pov(各 chunk 投票后的最终视角)
        baseline_json = json.dumps(world_baseline, ensure_ascii=False) if world_baseline else None
        graph_meta = graph_data.get("meta") if isinstance(graph_data.get("meta"), dict) else {}
        narrative_pov = graph_meta.get("narrative_pov") if isinstance(graph_meta, dict) else None
        if narrative_pov not in {"first", "second", "third", "mixed"}:
            narrative_pov = None
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE projects SET type=?, custom_type_name=?, tags=?, "
                "world_baseline_json=?, narrative_pov=?, updated_at=? "
                "WHERE id=?",
                (
                    inferred_type, inferred_custom,
                    json.dumps(cleaned_tags, ensure_ascii=False),
                    baseline_json,
                    narrative_pov,
                    iso_now(), job.project_id,
                ),
            )

        _emit(job_id, {
            "kind": "save_done",
            "characters_count": counts["characters"],
            "relationships_count": counts["relationships"],
            "events_count": counts["events"],
            "skipped_count": counts["skipped"],
        })

        # P0L.3(2026-05-24)— 主角缺位 post-check
        # 治"雪国岛村漏抽"场景:用户在 EntityReviewModal 没点补漏抽(或 detect 时阈值过严漏掉)
        # 写库后再扫一遍,若 narrative_pov="third" 且仍有 missed_persons → emit warning
        try:
            # 拉项目最终 narrative_pov + 所有 PERSON
            proj_row = fetch_one(
                conn,
                "SELECT narrative_pov FROM projects WHERE id=?",
                (job.project_id,),
            )
            project_pov = (proj_row["narrative_pov"] if proj_row else None) or ""

            if project_pov == "third":
                from app.services.llm_extract import detect_missed_persons as _detect_missed_post

                char_rows_post = fetch_all(
                    conn,
                    "SELECT name, identity, aliases_json FROM characters WHERE project_id=?",
                    (job.project_id,),
                )
                # 构造 build_graph 风格的 entities dict 给 detect 用
                merged_post: dict[str, dict] = {}
                for r in char_rows_post:
                    try:
                        ali = json.loads(r["aliases_json"] or "[]")
                        if not isinstance(ali, list):
                            ali = []
                    except (TypeError, ValueError):
                        ali = []
                    merged_post[r["name"]] = {
                        "name": r["name"],
                        "type": "PERSON",
                        "aliases": ali,
                        "description": r["identity"] or "",
                    }

                missed_post = _detect_missed_post(merged_post, text)
                if missed_post:
                    import logging
                    logging.warning(
                        f"[P0L.3] project {job.project_id} 主角缺位 post-check 发现"
                        f"{len(missed_post)} 个疑似漏抽: {missed_post[:5]}"
                    )
                    _emit(job_id, {
                        "kind": "missed_protagonist_warning",
                        "missed_persons": missed_post,
                        "narrative_pov": project_pov,
                    })
        except Exception as e:  # noqa: BLE001
            import logging
            logging.warning(f"[P0L.3] post-check failed: {e}")

        # === 完成 === (条件 UPDATE 防被 reset 后又写回 done)
        cost_yuan = _estimate_actual_cost(total_in_tokens, total_out_tokens)
        completed_at = iso_now()
        with transaction(conn) as tx:
            updated = execute(
                tx,
                "UPDATE graph_extraction_jobs SET state='done', "
                "characters_count=?, relationships_count=?, events_count=?, skipped_count=?, "
                "tokens_input=?, tokens_output=?, cost_yuan=?, completed_at=? "
                "WHERE id=? AND state NOT IN ('done', 'failed')",
                (
                    counts["characters"], counts["relationships"],
                    counts["events"], counts["skipped"],
                    total_in_tokens, total_out_tokens, cost_yuan,
                    completed_at, job_id,
                ),
            )
            if updated:
                # 仅在确实写到 done 时翻 upload(防被 reset 后 upload 还要 'parsed')
                execute(
                    tx, "UPDATE uploads SET state='ready' WHERE id=?", (job.upload_id,),
                )

        # Sprint C.2(2026-05-13)— credit 真扣:抽图谱完成后按真实总 token 算
        # 失败不阻塞 job done(LLM 已跑成功,credit 记账失败仅 warn)
        if updated:
            try:
                from app.services.credit_service import (
                    consume_credits,
                    credit_units_for_text_call,
                )
                units = credit_units_for_text_call(total_in_tokens, total_out_tokens)
                consume_credits(
                    conn,
                    user_id=job.user_id,
                    action="extract",
                    units=units,
                    related_id=job_id,
                    cost_yuan=cost_yuan,
                    metadata={
                        "input_tokens": total_in_tokens,
                        "output_tokens": total_out_tokens,
                        "vendor": "deepseek",
                        "upload_id": job.upload_id,
                    },
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"consume_credits(extract) failed for job {job_id}: {e}"
                )

        # P0J.4(2026-05-24)— 生命状态判定 hook(**必须在 enricher 之前**):
        # 对每个 PERSON 角色,基于全文采样判定 alive/deceased/in_facility/absent/unknown
        # 治"build_graph 单 chunk 视野判生死不准 + 漏抽边缘 PERSON"两个 bug
        # 注意:必须在 agent_profile_enricher 之前跑 — enricher 的 P0F.2 逻辑会
        #      用 6 段采样判 life_status,先跑就会被它的"跳过非默认值"挡住 inferer
        # 失败不阻塞 done state。
        if updated:
            try:
                from app.services.life_status_inferer import (
                    infer_all_characters_in_project,
                )

                # P0K.1(2026-05-24)— 进度回调:每完成 1 个角色 emit 1 个 SSE 事件
                # 防 SSE 通道死寂(原 bug:30-60s 无事件 → SSE 超时 → 前端漏接 done)
                def _life_status_progress(
                    name: str, status: str, idx: int, total: int,
                ) -> None:
                    _emit(job_id, {
                        "kind": "life_status_done",
                        "name": name,
                        "life_status": status,
                        "progress": idx,
                        "total": total,
                    })

                life_status_report = infer_all_characters_in_project(
                    conn, job.project_id,
                    emit_progress=_life_status_progress,
                )
                import logging
                logging.info(
                    f"[P0J.4 hook] project={job.project_id} life_status: "
                    f"inferred={life_status_report.get('inferred_count', 0)} "
                    f"skipped={life_status_report.get('skipped_count', 0)} "
                    f"errors={life_status_report.get('errors_count', 0)}"
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"[P0J.4 hook] life_status_inferer failed for project "
                    f"{job.project_id}: {e}(图谱抽取仍按 done 流程走,不阻塞)"
                )

        # P0M.1(2026-05-24)— 性别 / 亲属审计 hook
        # 在 life_status_inferer 之后跑(都是 description 后审计类)
        # 治"师傅=父亲 vs 母亲"性别脑补 bug — LLM 二次审计 source_context 校验
        if updated:
            try:
                from app.services.gender_audit import (
                    audit_all_characters_in_project as _gender_audit_all,
                )

                def _gender_progress(
                    name: str, verdict: str, idx: int, total: int,
                ) -> None:
                    _emit(job_id, {
                        "kind": "gender_audit_done",
                        "name": name,
                        "verdict": verdict,
                        "progress": idx,
                        "total": total,
                    })

                gender_report = _gender_audit_all(
                    conn, job.project_id,
                    emit_progress=_gender_progress,
                )
                import logging
                logging.info(
                    f"[P0M.1 hook] project={job.project_id} gender_audit: "
                    f"audited={gender_report.get('audited', 0)} "
                    f"fixed={gender_report.get('fixed', 0)} "
                    f"errors={gender_report.get('errors', 0)}"
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"[P0M.1 hook] gender_audit failed for project "
                    f"{job.project_id}: {e}(图谱抽取仍按 done 流程走,不阻塞)"
                )

        # Sprint 6.A1(2026-05-18)— 多 agent 仿真前置 hook:
        # 抽完图谱后自动跑 agent_profile_enricher(LLM 补全角色档案 4 字段)
        # + protagonist_judger(4 维度评分判定主角)。失败不阻塞抽取 done state。
        # P0J.4 起:此 hook 在 life_status_inferer 之后跑,enricher 会跳过已填的 life_status
        if updated:
            try:
                from app.services.agent_profile_enricher import (
                    enrich_all_characters_in_project,
                )
                from app.services.protagonist_judger import judge_protagonists
                enrich_report = enrich_all_characters_in_project(conn, job.project_id)
                judge_report = judge_protagonists(conn, job.project_id)
                import logging
                logging.info(
                    f"[Sprint 6.A1 hook] project={job.project_id} "
                    f"enriched={enrich_report.get('enriched_count', 0)} "
                    f"protagonists={judge_report.get('protagonist_count', 0)}"
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"[Sprint 6.A1 hook] enrich+judge failed for project "
                    f"{job.project_id}: {e}(图谱抽取仍按 done 流程走,不阻塞)"
                )

        # Sprint 6.A2 M2(2026-05-18)— 场景图谱 hook:
        # 扫 extract_chunk_results 抽 LOCATION 实体 + 去重 + 落 project_scenes 表。
        # 零 LLM 调用(直接用 build_graph 已抽的实体),失败不阻塞。
        if updated:
            try:
                from app.services.scene_extractor import (
                    extract_scenes_from_project,
                )
                scene_report = extract_scenes_from_project(conn, job.project_id)
                import logging
                logging.info(
                    f"[Sprint 6.A2 M2 hook] project={job.project_id} "
                    f"scenes={scene_report.get('scenes_count', 0)} "
                    f"chunks={scene_report.get('chunks_processed', 0)} "
                    f"raw_locations={scene_report.get('raw_locations_seen', 0)}"
                )
            except Exception as e:  # noqa: BLE001
                import logging
                logging.warning(
                    f"[Sprint 6.A2 M2 hook] scene extraction failed for project "
                    f"{job.project_id}: {e}(图谱抽取仍按 done 流程走,不阻塞)"
                )

        _emit(job_id, {
            "kind": "done",
            "characters_count": counts["characters"],
            "relationships_count": counts["relationships"],
            "events_count": counts["events"],
            "skipped_count": counts["skipped"],
            "cost_yuan": round(cost_yuan, 4),
        })

    except InterruptedError as ie:
        # 用户主动 reset 触发 — db 已经被 reset_extract_job 改成 failed,worker 静默退出
        import logging
        logging.info("extract job %s 被取消: %s", job_id, ie)
        # 不调 _mark_failed,不 emit error(reset 已经 emit 过了)
    except Exception as e:  # noqa: BLE001
        import logging, traceback
        traceback.print_exc()
        logging.error("run_extract %s 失败: %s", job_id, e)
        err_msg = f"{type(e).__name__}: {e}"[:300]
        _mark_failed(conn, job_id, err_msg)
        _emit(job_id, {"kind": "error", "message": _humanize_error(err_msg)})
    finally:
        _unregister_worker(job_id)
        conn.close()


# ======================================================================
# save 子流程
# ======================================================================

def _save_to_project(
    conn: sqlite3.Connection,
    project_id: str,
    entities: list[dict],
    relations: list[dict],
    profiles_by_name: dict[str, dict],
    top_persons: list[dict],
    minimal_profiles_by_name: dict[str, dict] | None = None,
) -> dict[str, int]:
    """batch INSERT characters / relationships / events,返回计数。

    重抽时按 character.name 项目内去重,skipped 计数。

    profile 三层降级(2026-05 方案 D):
      1. profiles_by_name(top 30 主角)— character_generator 精品输出,
         含 voice_fingerprint.quotes / no_go_list 等完整字段
      2. minimal_profiles_by_name(配角)— character_minimal_profile.md 批量轻量,
         含 personality / quotes / no_go_list 简略版
      3. identity-only(双重 fallback)— LLM 全炸了 / 配角批失败时降级
    """
    minimal_profiles_by_name = minimal_profiles_by_name or {}
    # 拉项目内已有 characters 名字 + aliases(去重 + FOCUS.11 跨 job 归一用)
    existing_chars = fetch_all(
        conn,
        "SELECT id, name, aliases_json FROM characters WHERE project_id=?",
        (project_id,),
    )
    existing_name_to_id: dict[str, str] = {r["name"]: r["id"] for r in existing_chars}

    # FOCUS.11(2026-05-22):构建"任一名字 → 已有 character id"反向索引(name + aliases)
    # 治"中年艺妓被旧 job 入库,新 job LLM 给驹子加 alias=['中年艺妓'] 但 save 时仅 name 匹配
    # → 两者各自独立残留"实战 bug。
    existing_alias_index: dict[str, str] = {}   # name/alias → char_id
    existing_aliases_by_id: dict[str, set[str]] = {}   # char_id → 当前 aliases set(供累积)
    for r in existing_chars:
        cid = r["id"]
        existing_alias_index[r["name"]] = cid
        try:
            old_aliases = json.loads(r["aliases_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            old_aliases = []
        old_set: set[str] = {str(a).strip() for a in old_aliases if isinstance(a, str) and a.strip()}
        existing_aliases_by_id[cid] = old_set
        for a in old_set:
            existing_alias_index.setdefault(a, cid)   # name 优先,alias 不覆盖 name

    def _find_existing_match(new_name: str, new_aliases: list[str]) -> Optional[str]:
        """FOCUS.11:查找新 entity 是否与已有 character 等价。返回已有 char_id 或 None。

        判定(对齐 _post_merge_alias_dedup PERSON 规则):
          ① 新 name 在 existing index 里 → 匹配(覆盖老 name 匹配语义)
          ② 新 aliases 任一在 existing index 里 → 匹配
          ③ 新 name 是某已有 name 的真子串(≥ 2 字)或反之 → 匹配
          ④ 已有 character.aliases 含新 name → 匹配
        命中即返回该 char_id;未命中返回 None。
        """
        if new_name in existing_alias_index:
            return existing_alias_index[new_name]
        for a in new_aliases:
            if a in existing_alias_index:
                return existing_alias_index[a]
        # 子串归一(限短 ≥ 2 字 + 不与单字"我"等碰撞):
        if len(new_name) >= 2:
            for ex_name in existing_name_to_id:
                if ex_name == new_name:
                    continue
                shorter, longer = sorted((new_name, ex_name), key=len)
                if len(shorter) >= 2 and shorter in longer:
                    return existing_name_to_id[ex_name]
        return None

    def _merge_aliases_into_existing(
        existing_id: str, new_name: str, new_aliases: list[str], tx_local,
        new_life_status: Optional[str] = None,  # P0I.3
        new_status_note: Optional[str] = None,  # P0I.3
    ) -> None:
        """把新 entity 的 name + aliases 累积到旧 character 的 aliases_json(不覆盖 personality 等)。

        P0I.3(2026-05-24):同时更新 life_status / status_note(取最严重)
        — 增量抽取新 chunk 见到 deceased 时,必须传播到已存的 character 行
        """
        merged_set = set(existing_aliases_by_id.get(existing_id, set()))
        # 找到旧 character 的 name(防把自己的 name 加进自己 aliases)
        existing_name = next(
            (n for n, cid in existing_name_to_id.items() if cid == existing_id), ""
        )
        # P0L.9(2026-05-24)— 增量合并路径也跑 _is_invalid_alias 清洗
        # 治"叶子弟弟"漏网:增量抽取走这个路径,绕过了 _post_merge_alias_dedup 的清洗
        # 把新 name + new_aliases 都跑一遍清洗后再累积
        from app.services.llm_extract import _is_invalid_alias
        if new_name and new_name != existing_name:
            if not _is_invalid_alias(existing_name, new_name):
                merged_set.add(new_name)
        for a in new_aliases:
            if a and a != existing_name and not _is_invalid_alias(existing_name, a):
                merged_set.add(a)
        # 同时清洗已存的 aliases(治"重抽时老数据残留"问题)
        merged_set = {
            a for a in merged_set
            if not _is_invalid_alias(existing_name, a)
        }
        merged_list = sorted(merged_set)[:15]   # 上限 15(对齐 character_merger)

        # P0I.3 — 仅当新值"更严重"时更新 life_status / status_note,否则保留旧值
        # 严重度:deceased > in_facility > absent > unknown > alive
        if new_life_status in ("alive", "deceased", "in_facility", "absent", "unknown"):
            from app.services.llm_extract import _life_status_severity
            old_row = fetch_one(
                tx_local,
                "SELECT life_status, status_note FROM characters WHERE id=?",
                (existing_id,),
            )
            old_status = (old_row["life_status"] if old_row else "alive") or "alive"
            if _life_status_severity(new_life_status) > _life_status_severity(old_status):
                execute(
                    tx_local,
                    "UPDATE characters SET aliases_json=?, life_status=?, status_note=?, updated_at=? "
                    "WHERE id=?",
                    (
                        json.dumps(merged_list, ensure_ascii=False),
                        new_life_status,
                        (new_status_note or "").strip()[:200],
                        iso_now(), existing_id,
                    ),
                )
            else:
                execute(
                    tx_local,
                    "UPDATE characters SET aliases_json=?, updated_at=? WHERE id=?",
                    (json.dumps(merged_list, ensure_ascii=False), iso_now(), existing_id),
                )
        else:
            # 无 life_status 信息时,只更新 aliases(老路径)
            execute(
                tx_local,
                "UPDATE characters SET aliases_json=?, updated_at=? WHERE id=?",
                (json.dumps(merged_list, ensure_ascii=False), iso_now(), existing_id),
            )
        # 更新内存索引(后续 entity 还能匹配上)
        existing_aliases_by_id[existing_id] = merged_set
        for a in merged_set:
            existing_alias_index.setdefault(a, existing_id)

    name_to_new_char_id: dict[str, str] = {}   # 新建 char 的 name → id 映射(给 relations 用)
    # FOCUS.11:被合并到已有 char 的新 name → existing char_id(给 relations 重定向)
    new_name_to_merged_existing_id: dict[str, str] = {}
    skipped = 0
    char_count = 0
    rel_count = 0
    evt_count = 0
    now = iso_now()

    person_entities = [e for e in entities if e.get("type") == "PERSON"]
    top_person_names = {ent["name"] for ent in top_persons}

    with transaction(conn) as tx:
        # 1. PERSON → characters
        for ent in person_entities:
            name = ent.get("name")
            if not name:
                continue
            # FOCUS.11:跨 job 归一 — 提前解析 aliases(供 _find_existing_match 用)
            raw_aliases_for_match = ent.get("aliases") or []
            aliases_for_match = [
                str(a).strip() for a in raw_aliases_for_match
                if isinstance(a, str) and a.strip()
            ]
            matched_existing_id = _find_existing_match(name, aliases_for_match)
            if matched_existing_id:
                # 命中已有 character → 累积 aliases(不覆盖其他字段)+ skip 新建
                # P0I.3(2026-05-24):同时传 life_status / status_note(merge 取最严重)
                _merge_aliases_into_existing(
                    matched_existing_id, name, aliases_for_match, tx,
                    new_life_status=ent.get("life_status"),
                    new_status_note=ent.get("status_note"),
                )
                new_name_to_merged_existing_id[name] = matched_existing_id
                skipped += 1
                continue   # FOCUS.11:含老版本"name 命中"语义,扩展到 alias / 子串

            char_id = str(uuid.uuid4())
            name_to_new_char_id[name] = char_id

            # 三层降级(方案 D):top 30 精品 → 配角批量轻量 → identity-only
            profile = profiles_by_name.get(name)
            behavior_baseline: Optional[dict] = None   # FOCUS.7 默认 None(配角 / identity-only 走 fallback)
            if profile and isinstance(profile, dict):
                # 第 1 层:top 30 主角 — character_generator 完整档案
                identity = str(profile.get("identity") or ent.get("description") or "")[:200]
                personality = str(profile.get("personality") or "")[:500]
                vf = profile.get("voice_fingerprint") or {}
                quotes_raw = vf.get("quotes") if isinstance(vf, dict) else None
                if not isinstance(quotes_raw, list):
                    quotes_raw = []
                # FOCUS.5(2026-05-22):quotes 高相似度去重,避免 LLM 重复抽同角色单调台词
                # (实测《挪威的森林》"突击队"角色被抽出 12 句口吃台词全是"地、地、地图"变种)
                quotes = [str(q)[:200] for q in quotes_raw][:20]
                quotes = _dedupe_similar_quotes(quotes)
                no_go_raw = profile.get("no_go_list")
                if not isinstance(no_go_raw, list):
                    no_go_raw = []
                no_go = [str(n)[:200] for n in no_go_raw][:20]
                # FOCUS.7(2026-05-22):top 30 主角抽取 behavior_baseline 4 维(配角不抽,minimal_profile 不输出此字段)
                behavior_baseline = _extract_behavior_baseline_from_profile(profile)
            else:
                minimal = minimal_profiles_by_name.get(name)
                if minimal and isinstance(minimal, dict):
                    # 第 2 层:配角 — character_minimal_profile 批量轻量
                    identity = str(ent.get("description") or "")[:200]
                    personality = str(minimal.get("personality") or "")[:500]
                    quotes_raw = minimal.get("quotes")
                    if not isinstance(quotes_raw, list):
                        quotes_raw = []
                    quotes = [str(q)[:200] for q in quotes_raw][:20]
                    quotes = _dedupe_similar_quotes(quotes)   # FOCUS.5
                    no_go_raw = minimal.get("no_go_list")
                    if not isinstance(no_go_raw, list):
                        no_go_raw = []
                    no_go = [str(n)[:200] for n in no_go_raw][:20]
                else:
                    # 第 3 层:identity-only(批量补全失败时的双重 fallback)
                    identity = str(ent.get("description") or "")[:200]
                    personality = ""
                    quotes = []
                    no_go = []

            # Sprint 6.A2 FOCUS(2026-05-21):写入 entities[].aliases 到 aliases_json
            # build_graph prompt v4 起强化称谓归一,LLM 输出会带 aliases:[]
            aliases_raw = ent.get("aliases")
            if not isinstance(aliases_raw, list):
                aliases_list: list[str] = []
            else:
                # 去重 + 单条 ≤ 30 字 + list ≤ 15(上限对齐 character_merger)
                seen_alias: set[str] = set()
                aliases_list = []
                for a in aliases_raw:
                    s = str(a).strip()[:30]
                    if s and s != name and s not in seen_alias:
                        seen_alias.add(s)
                        aliases_list.append(s)
                aliases_list = aliases_list[:15]

            # P0I.3(2026-05-24)— 抽取 life_status / status_note 写入 characters 表
            # _merge_graphs 已跨 chunk 取最严重值,这里直接消费
            raw_life_status = ent.get("life_status")
            if isinstance(raw_life_status, str) and raw_life_status in (
                "alive", "deceased", "in_facility", "absent", "unknown"
            ):
                life_status_val = raw_life_status
            else:
                life_status_val = "alive"  # 老数据兼容兜底
            status_note_val = str(ent.get("status_note") or "").strip()[:200]

            execute(
                tx,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at, "
                " aliases_json, behavior_baseline_json, life_status, status_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, ?, ?, ?, ?, ?)",
                (
                    char_id, project_id, name, identity, personality,
                    json.dumps(quotes, ensure_ascii=False),
                    json.dumps(no_go, ensure_ascii=False),
                    now, now,
                    json.dumps(aliases_list, ensure_ascii=False) if aliases_list else None,
                    # FOCUS.7(2026-05-22):top 30 主角持久化 baseline 4 维(配角 / fallback 写 NULL)
                    json.dumps(behavior_baseline, ensure_ascii=False) if behavior_baseline else None,
                    # P0I.3(2026-05-24):build_graph 阶段抽出的生命状态
                    life_status_val, status_note_val,
                ),
            )
            char_count += 1

        # name → id 完整映射(已存在的 + 新建的 + FOCUS.11 跨 job 归一重定向)
        # 老语义:full_name_to_id 把 entity 的 name 映射到 char_id(已有 or 新建)
        # FOCUS.11 增量:被合并到已有 char 的新 name(如新 entity"驹姐"被并到旧"驹子")
        # 要重定向到 existing_id,这样 relations.source="驹姐" 也能找到对应 char_id
        full_name_to_id = {
            **existing_name_to_id,
            **name_to_new_char_id,
            **new_name_to_merged_existing_id,
        }

        # 2. relations(只入人际关系,过滤掉"参与"/"位于"/"拥有"/"提及")
        seen_rel_keys: set[tuple[str, str, str]] = set()
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            src = rel.get("source")
            tgt = rel.get("target")
            rel_type_raw = rel.get("type")
            if not src or not tgt or not rel_type_raw:
                continue
            if not is_interpersonal_relation(rel_type_raw):
                continue   # "参与"/"位于"/"拥有"/"提及" 不入 relationships 表
            src_id = full_name_to_id.get(src)
            tgt_id = full_name_to_id.get(tgt)
            if not src_id or not tgt_id or src_id == tgt_id:
                continue
            rel_type = normalize_relation_type(rel_type_raw)
            # 简单去重(同源同标同类型只一条)
            key = (src_id, tgt_id, rel_type)
            if key in seen_rel_keys:
                continue
            seen_rel_keys.add(key)
            # Sprint 3.A polish:strength 已在 _merge_graphs 内归一化到白名单,
            # 直接取即可;老数据(无 strength)走 fallback 'moderate'
            strength = rel.get("strength") or "moderate"
            execute(
                tx,
                "INSERT INTO relationships "
                "(id, project_id, source_id, target_id, type, description, strength, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), project_id, src_id, tgt_id, rel_type,
                    str(rel.get("description") or "")[:500], strength, now,
                ),
            )
            rel_count += 1

        # 3. EVENT → events;participants 来自 build_graph relations 中的"参与"类型
        event_entities = [e for e in entities if e.get("type") == "EVENT"]
        # 收集每个 event 的参与者 name(从"参与"类型 relations 提取)
        event_to_participants: dict[str, list[str]] = {}
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            if rel.get("type") != "参与":
                continue
            src = rel.get("source")  # 通常是 PERSON
            tgt = rel.get("target")  # 通常是 EVENT
            if not src or not tgt:
                continue
            # 不确定 src/tgt 哪个是 PERSON;两边都查
            for evt_name, person_name in ((tgt, src), (src, tgt)):
                if any(e.get("name") == evt_name and e.get("type") == "EVENT" for e in entities):
                    if person_name in full_name_to_id:
                        event_to_participants.setdefault(evt_name, []).append(person_name)

        for ent in event_entities:
            evt_name = ent.get("name")
            if not evt_name:
                continue
            participant_names = event_to_participants.get(evt_name, [])
            participant_ids = [
                full_name_to_id[n] for n in participant_names if n in full_name_to_id
            ]
            description = str(ent.get("description") or evt_name)[:300]
            execute(
                tx,
                "INSERT INTO events "
                "(id, project_id, description, participants, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), project_id, description,
                    json.dumps(participant_ids, ensure_ascii=False),
                    now,
                ),
            )
            evt_count += 1

    return {
        "characters": char_count,
        "relationships": rel_count,
        "events": evt_count,
        "skipped": skipped,
    }


# ======================================================================
# 状态机辅助
# ======================================================================

def _fetch_job(conn: sqlite3.Connection, job_id: str) -> Optional[ExtractJob]:
    row = fetch_one(
        conn, "SELECT * FROM graph_extraction_jobs WHERE id=?", (job_id,)
    )
    return ExtractJob.from_row(row) if row else None


def _set_state(conn: sqlite3.Connection, job_id: str, state: str) -> None:
    """状态推进 — 条件 UPDATE 防被 reset 后又被 worker 写回 extracting_*。

    若 db 中 state 已是 done/failed(被 reset 了),本次 UPDATE 不影响任何行,
    SSE 也不推 state_change(避免误导前端 / 但已注册的 worker 仍会继续跑直到下次 cancel 检查)。
    """
    affected = 0
    with transaction(conn) as tx:
        affected = execute(
            tx,
            "UPDATE graph_extraction_jobs SET state=? WHERE id=? AND state NOT IN ('done', 'failed')",
            (state, job_id),
        )
    if affected:
        _emit(job_id, {"kind": "state_change", "state": state})


def _save_intermediate(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    extracted_graph_json: Optional[str] = None,
    inferred_type: Optional[str] = None,
    inferred_custom_type_name: Optional[str] = None,
    inferred_tags_json: Optional[str] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
) -> None:
    """部分字段更新(只动非 None 的)。"""
    sets: list[str] = []
    params: list[Any] = []
    if extracted_graph_json is not None:
        sets.append("extracted_graph_json=?"); params.append(extracted_graph_json)
    if inferred_type is not None:
        sets.append("inferred_type=?"); params.append(inferred_type)
    if inferred_custom_type_name is not None:
        sets.append("inferred_custom_type_name=?"); params.append(inferred_custom_type_name)
    if inferred_tags_json is not None:
        sets.append("inferred_tags_json=?"); params.append(inferred_tags_json)
    if tokens_input is not None:
        sets.append("tokens_input=?"); params.append(tokens_input)
    if tokens_output is not None:
        sets.append("tokens_output=?"); params.append(tokens_output)
    if not sets:
        return
    params.append(job_id)
    with transaction(conn) as tx:
        execute(
            tx, f"UPDATE graph_extraction_jobs SET {', '.join(sets)} WHERE id=?",
            tuple(params),
        )


def _humanize_error(raw_error: str) -> str:
    """把内部技术错误转成用户能看懂的友好文案。

    对原文本做特征匹配,而非完全替换 — 留下 1 行内部提示给排错(开发模式下日志仍有完整 traceback)。
    用户视角不该看到 'LlmJsonParseFailed' / 'raw 前 200 字' / 'line 595 column 21' 这种细节。
    """
    if not raw_error:
        return "抽取失败,请稍后重试"
    lower = raw_error.lower()
    if "llmjsonparsefailed" in lower or "json 解析失败" in raw_error or "json decode" in lower:
        return "AI 输出格式异常 — 通常重试一次就好,如反复失败可先把文件切短再传"
    if "llmcallfailed" in lower or "llm 调用失败" in raw_error or "timeout" in lower:
        return "AI 服务暂时不可用,请稍后重试"
    if "quota" in lower or "配额" in raw_error:
        return "本月推演配额不足"
    if "读上传文件失败" in raw_error or "无法识别" in raw_error:
        return "源文件读取失败,可能损坏或编码异常"
    # 兜底:截断技术细节,只留前 60 字给用户参考
    return raw_error[:60] + ("…" if len(raw_error) > 60 else "")


def _mark_failed(
    conn: sqlite3.Connection, job_id: str, error_message: str
) -> None:
    """job state='failed' + 错误落库(转译为用户文案)+ uploads.state='failed'。

    条件 UPDATE 防被 reset 后又被 worker 强行 mark 另一个错误覆盖。
    """
    completed_at = iso_now()
    job = _fetch_job(conn, job_id)
    upload_id = job.upload_id if job else None
    user_friendly = _humanize_error(error_message)
    with transaction(conn) as tx:
        affected = execute(
            tx,
            "UPDATE graph_extraction_jobs SET state='failed', error_message=?, "
            "completed_at=? WHERE id=? AND state NOT IN ('done', 'failed')",
            (user_friendly, completed_at, job_id),
        )
        if upload_id and affected:
            # 只在确实改了状态时才把 upload 标 failed(防覆盖 reset 已设的 'parsed')
            execute(
                tx, "UPDATE uploads SET state='failed', error_message=? WHERE id=?",
                (user_friendly, upload_id),
            )


def _estimate_actual_cost(in_tokens: int, out_tokens: int) -> float:
    """用 llm_client.estimate_cost_yuan 算实际成本。"""
    from app.services.llm_client import estimate_cost_yuan
    return estimate_cost_yuan(in_tokens, out_tokens)


# ======================================================================
# SSE 流(镜像 simulation_service.stream_simulation_state)
# ======================================================================

def _format_sse_event(data: dict) -> str:
    """SSE 单条事件序列化:`data: {json}\\n\\n`。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _job_snapshot_payload(job: ExtractJob) -> dict:
    """SSE 启动首发 snapshot — 让客户端立刻拿到当前完整状态。"""
    return {
        "kind": "snapshot",
        "id": job.id,
        "state": job.state,
        "characters_count": job.characters_count,
        "relationships_count": job.relationships_count,
        "events_count": job.events_count,
        "skipped_count": job.skipped_count,
        "tokens_input": job.tokens_input,
        "tokens_output": job.tokens_output,
        "cost_yuan": round(job.cost_yuan, 4),
        "error_message": job.error_message,
        "is_admin_retag": job.is_admin_retag,
    }


async def stream_extract_state(job_id: str, user_id: str):
    """async generator — SSE handler 调,产 text/event-stream 字节流。

    流程同 simulation 1.L:鉴权 + 首发 snapshot + 终态直接 return / 否则注册 queue
    + 30s 心跳 + 终态事件后 break。
    """
    conn = get_connection()
    try:
        # 1. 鉴权 + 首发 snapshot
        job = get_extract_job_or_404(conn, job_id, user_id)
        yield _format_sse_event(_job_snapshot_payload(job)).encode("utf-8")

        if job.state in ("done", "failed"):
            return

        # 2. 注册订阅
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        _register_subscriber(job_id, queue, loop)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield b": heartbeat\n\n"
                    continue

                yield _format_sse_event(event).encode("utf-8")

                if event.get("kind") in TERMINAL_EVENT_KINDS:
                    break
        finally:
            _unregister_subscriber(job_id, queue)
    finally:
        conn.close()


__all__ = [
    "UploadNotExtractable",
    "ExtractJobNotResumable",
    "ExtractJobNotResettable",
    "trigger_extract",
    "reset_extract_job",
    "resume_extract_job",
    "run_extract",
    "kick_off_extract",
    "get_extract_job_or_404",
    "list_project_extract_jobs",
    "stream_extract_state",
    "is_job_alive",
]
