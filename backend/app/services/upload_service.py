"""文件上传服务 — Sprint 2.A 中间态入口。

主流程(POST /api/projects/{project_id}/uploads):
  1. 校验大小 + 格式(白名单 mime + 扩展名双重)
  2. SHA256 计算 + 同用户去重(撞 → 409 + 旧 upload_id)
  3. 临时存盘 → 解析纯文本(file_parser)
  4. 红旗扫描(red_flag_filter)
     - 命中 block → 落 violation_logs + 删临时文件 + raise UploadRejectedRedFlag
  5. 移到正式存储位置 backend/data/uploads/{user_id}/{upload_id}.{ext}
  6. 落 uploads 行 state=parsed,返回 upload_id

异常:
  ResourceNotFoundOrForbidden  project 不属于该用户
  UploadFormatRejected         大小超限 / mime 非白名单 / 扩展名不匹配
  UploadDuplicate              SHA256 撞,detail 含旧 upload_id
  UploadParseFailed            file_parser 失败
  UploadRejectedRedFlag        红旗命中,detail 含 category + matched_text
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.models.upload import Upload
from app.services.file_parser import (
    EXT_TO_MIME,
    SUPPORTED_MIMES,
    detect_ext,
    parse_file,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
    iso_now,
)
from app.services.red_flag_filter import filter_text


# === 异常 ===

class UploadFormatRejected(Exception):
    """大小超限 / mime 不在白名单 / 扩展名与 mime 不匹配。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class UploadDuplicate(Exception):
    """SHA256 撞同用户已上传过的内容。detail 提供旧 upload_id。"""

    def __init__(self, existing_upload_id: str):
        super().__init__(f"已上传过相同内容的文件 (upload_id={existing_upload_id})")
        self.existing_upload_id = existing_upload_id


class UploadParseFailed(Exception):
    """file_parser 抽取失败 — 文件损坏 / 编码不识别 / 内嵌结构异常。"""


class UploadRejectedRedFlag(Exception):
    """红旗命中 → 拒收 + 落 violation_logs。"""

    def __init__(self, category: str, matched_text: str, flag_id: str):
        super().__init__(f"内容含红旗词({category}):{matched_text}")
        self.category = category
        self.matched_text = matched_text
        self.flag_id = flag_id


class ProjectAlreadyHasUpload(Exception):
    """Sprint 6.A2 M7.E(2026-05-20)— 同 project 已有 upload,拒绝第二次上传。

    产品定位:一个项目 = 一个作品世界。同项目上传多本不同作品会导致 characters /
    relationships / events 全局混合 + project.world_baseline 被覆盖,续写质量崩坏。
    用户想换作品 → 新建项目;想替换当前 upload → 先 DELETE 现有 upload 再上传。
    """

    def __init__(self, existing_upload_id: str, existing_filename: str):
        super().__init__(
            f"本项目已有作品《{existing_filename}》— 一个项目只能装一部作品"
        )
        self.existing_upload_id = existing_upload_id
        self.existing_filename = existing_filename


@dataclass
class UploadRequest:
    """业务层请求 DTO,从 router 的 UploadFile 转换而来。"""

    project_id: str
    user_id: str
    filename: str
    declared_mime: str
    raw_bytes: bytes


# === 辅助 ===

def _user_uploads_dir(user_id: str) -> Path:
    """用户专属上传目录(运行时按需建)。"""
    p = settings.uploads_abs_dir / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _find_existing_by_sha(
    conn: sqlite3.Connection, user_id: str, sha: str
) -> Optional[Upload]:
    row = fetch_one(
        conn,
        "SELECT * FROM uploads WHERE user_id=? AND sha256=?",
        (user_id, sha),
    )
    return Upload.from_row(row) if row else None


def _log_violation(
    conn: sqlite3.Connection,
    user_id: str,
    upload_id: Optional[str],
    ip_address: Optional[str],
    flag_id: str,
    matched_text: str,
) -> None:
    """落 violation_logs(法务证据 trail,失败不阻塞上传拒绝路径,但要记 warning)。"""
    try:
        with transaction(conn) as tx:
            execute(
                tx,
                "INSERT INTO violation_logs "
                "(id, user_id, upload_id, ip_address, flag_id, matched_text, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), user_id, upload_id, ip_address,
                    flag_id, matched_text[:80], iso_now(),
                ),
            )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning("log_violation 失败但不影响拒绝路径: %s", e)


# === 主流程 ===

def receive_upload(
    conn: sqlite3.Connection,
    req: UploadRequest,
    ip_address: Optional[str] = None,
) -> dict:
    """业务主入口。返回 Upload.to_response() 形 dict。"""
    # 1. 鉴权 — 项目必须属于当前用户
    get_project_or_403(conn, req.project_id, req.user_id)

    # 1.5. Sprint 6.A2 M7.E(2026-05-20)— 同 project 已有 upload 直接拒
    # 产品定位"一个项目 = 一个作品世界",防止多作品角色 / 关系 / 世界观混合
    # 用户想替换 → 先 DELETE 旧 upload(前端 UI 显示"重新上传"按钮编排两步走)
    existing = fetch_one(
        conn,
        "SELECT id, filename FROM uploads WHERE project_id=? LIMIT 1",
        (req.project_id,),
    )
    if existing is not None:
        raise ProjectAlreadyHasUpload(
            existing_upload_id=existing["id"],
            existing_filename=existing["filename"],
        )

    # 2. 大小校验
    size = len(req.raw_bytes)
    if size == 0:
        raise UploadFormatRejected("空文件")
    if size > settings.upload_max_bytes:
        max_mb = settings.upload_max_bytes // (1024 * 1024)
        raise UploadFormatRejected(
            f"文件超过 {max_mb}MB 上限 (实际 {size // (1024 * 1024)}MB)"
        )

    # 3. 类型校验 —— 扩展名为准(2026-05-28 修 .epub 误拒)
    #    背景:旧逻辑严格要求浏览器 content_type ∈ 白名单,但 Windows 浏览器上传 .epub
    #    常给 application/octet-stream(系统没注册 epub MIME)→ 正常 epub 被误拒.
    #    content_type 本可任意伪造,不是安全防线;真正的防线是解析阶段
    #    (parse_file 用 ebooklib/python-docx 解析,非法文件解析失败即拒).
    ext = detect_ext(req.filename)
    if not ext:
        raise UploadFormatRejected(
            f"文件名缺少扩展名或扩展名不在白名单({list(EXT_TO_MIME)})"
        )
    # 扩展名已过白名单 → 用它确定权威 mime(后续解析 + 落库都以此为准)
    effective_mime = EXT_TO_MIME[ext]
    # declared_mime 仅作"伪造扩展名"辅助检测:浏览器给了精确且冲突的白名单 mime 才拒
    #(如把 .txt 改名 .epub 上传,且浏览器如实报 text/plain);
    # 给 octet-stream / 空 / 同类 mime → 视为浏览器未识别,放行
    declared = (req.declared_mime or "").lower()
    if declared in SUPPORTED_MIMES and declared != effective_mime:
        raise UploadFormatRejected(
            f"扩展名 {ext!r} 与声明类型 {declared!r} 不一致(防伪造)"
        )

    # 4. SHA256 + 去重
    sha = _sha256(req.raw_bytes)
    dup = _find_existing_by_sha(conn, req.user_id, sha)
    if dup is not None:
        raise UploadDuplicate(dup.id)

    # 5. 临时存盘 → 解析
    upload_id = str(uuid.uuid4())
    user_dir = _user_uploads_dir(req.user_id)
    final_path = user_dir / f"{upload_id}{ext}"
    final_path.write_bytes(req.raw_bytes)

    parse_result = parse_file(final_path, effective_mime)
    if not parse_result.success:
        # 解析失败 → 删文件,不落 uploads 行
        _safe_unlink(final_path)
        raise UploadParseFailed(parse_result.error or "未知解析错误")

    # 6. 红旗扫描
    verdict = filter_text(conn, parse_result.text)
    if not verdict.passed:
        flag = verdict.matched_flag
        snippet = verdict.snippet or ""
        # 删文件 + 不落 uploads 行
        _safe_unlink(final_path)
        # 但要落 violation_logs(upload_id=None,因为没成功创建 upload 行)
        if flag is not None:
            _log_violation(
                conn, req.user_id, None, ip_address, flag.id, snippet,
            )
            raise UploadRejectedRedFlag(flag.category, snippet, flag.id)
        # 防御:不应到这里(passed=False 必带 matched_flag)
        raise UploadRejectedRedFlag("unknown", snippet, "")

    # 7. 落 uploads 行(state=parsed,准备 2.B 抽图谱接管)
    # storage_path 存"用户子路径 + 文件名"(uploads_abs_dir 相对),
    # 这样 OSS 化时只需改 uploads_abs_dir 解析方式,DB 不动
    storage_path = f"{req.user_id}/{upload_id}{ext}"
    uploaded_at = iso_now()
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO uploads "
            "(id, project_id, user_id, filename, storage_path, mime_type, "
            " size_bytes, sha256, parsed_text_chars, state, error_message, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsed', NULL, ?)",
            (
                upload_id, req.project_id, req.user_id, req.filename, storage_path,
                effective_mime, size, sha, len(parse_result.text), uploaded_at,
            ),
        )

    # 拉刚落库的行返回(口径与 GET 一致)
    row = fetch_one(conn, "SELECT * FROM uploads WHERE id=?", (upload_id,))
    return Upload.from_row(row).to_response()


def list_project_uploads(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> list[dict]:
    """列出项目下所有 uploads(按 uploaded_at DESC)。"""
    get_project_or_403(conn, project_id, user_id)
    rows = fetch_all(
        conn,
        "SELECT * FROM uploads WHERE project_id=? ORDER BY uploaded_at DESC",
        (project_id,),
    )
    return [Upload.from_row(r).to_response() for r in rows]


def list_ready_uploads_for_user(
    conn: sqlite3.Connection, user_id: str
) -> list[dict]:
    """Sprint 4.D(2026-05-13):列出当前用户所有 state='ready' 的 uploads,
    并 join projects 表带上 project_name(供漫画 modal 选源用)。

    为什么独立此 API:漫画态走独立 comic_projects 表,但 uploads 仍挂在 projects 下
    (历史原因);漫画 modal 选源时用户心智里没有"项目"概念,所以这层 join 是给用户
    "你之前在 X 项目上传的 Y 文件"的语义反馈,避免空 dropdown 让用户摸不着头脑。

    返回:[{id, filename, parsed_text_chars, project_id, project_name, uploaded_at}]
          按 uploaded_at DESC 排序;空列表合法(用户无 ready upload → modal 显引导文案)。
    """
    rows = fetch_all(
        conn,
        """SELECT u.id, u.filename, u.parsed_text_chars, u.project_id,
                  u.uploaded_at, p.name AS project_name
           FROM uploads u
           LEFT JOIN projects p ON u.project_id = p.id
           WHERE u.user_id = ? AND u.state = 'ready'
           ORDER BY u.uploaded_at DESC""",
        (user_id,),
    )
    return [
        {
            "id": r["id"],
            "filename": r["filename"],
            "parsed_text_chars": r["parsed_text_chars"],
            "project_id": r["project_id"],
            "project_name": r["project_name"] or "(项目已删除)",
            "uploaded_at": r["uploaded_at"],
        }
        for r in rows
    ]


def get_upload_or_404(
    conn: sqlite3.Connection, upload_id: str, user_id: str
) -> Upload:
    """取单条 + 鉴权(跨用户访问 → ResourceNotFoundOrForbidden,统一 404)。"""
    row = fetch_one(conn, "SELECT * FROM uploads WHERE id=?", (upload_id,))
    if not row:
        raise ResourceNotFoundOrForbidden("upload", upload_id)
    upload = Upload.from_row(row)
    if upload.user_id != user_id:
        raise ResourceNotFoundOrForbidden("upload", upload_id)
    return upload


def delete_upload(
    conn: sqlite3.Connection, upload_id: str, user_id: str
) -> None:
    """删 DB 行 + 删磁盘文件 + **级联清理项目内所有 extract 衍生数据**。

    Sprint 6.A2 FOCUS.5(2026-05-22):用户痛点 — 删除作品文件后,角色/关系/事件/场景/
    项目元数据(tags/world_baseline/narrative_pov)都还在,UI 仍显示"图谱已就绪",
    体感是项目还粘着旧作品的影子。修复:删除唯一 upload = "项目回到刚创建的空状态"
    (因为"一个项目 = 一个作品世界"是产品定位级 invariant)。

    清理范围(同 project_id 下):
      - characters(FK CASCADE 自动清 relationships / canonical_entities / action_ledger /
        emotional_state_chain / character_arcs / foreshadow_ledger /
        character_refinements)
      - events 表全部
      - project_scenes 表全部
      - extract_jobs(FK CASCADE 清 extract_chunk_results)
      - projects 表:tags=[] / world_baseline_json=NULL / narrative_pov=NULL
        (type / mode / name 保留 — 用户创建时拍板的硬属性)

    **不清理**:simulations / 续写产物(用户辛苦跑出来的,保留;允许孤儿 character_id
    引用)。若需清 simulations,用户在 SimulationsList 单独删除。

    文件已不在 → 静默(只删 DB)。
    """
    import json as _json
    upload = get_upload_or_404(conn, upload_id, user_id)
    project_id = upload.project_id
    abs_path = settings.uploads_abs_dir / upload.storage_path
    _safe_unlink(abs_path)

    with transaction(conn) as tx:
        # ① 删 upload 本身
        execute(tx, "DELETE FROM uploads WHERE id=?", (upload_id,))

        # ② 级联清同项目下抽取衍生数据
        # characters 删 → FK CASCADE 清 relationships / canonical_entities / action_ledger 等
        execute(tx, "DELETE FROM characters WHERE project_id=?", (project_id,))
        # events 没 FK CASCADE,手动清
        execute(tx, "DELETE FROM events WHERE project_id=?", (project_id,))
        # project_scenes 同上
        execute(tx, "DELETE FROM project_scenes WHERE project_id=?", (project_id,))
        # graph_extraction_jobs CASCADE 清 chunk_results
        execute(
            tx,
            "DELETE FROM graph_extraction_jobs WHERE project_id=?",
            (project_id,),
        )

        # ③ 重置 project "被 LLM 抽取填充的元数据"(type/mode/name 不动)
        execute(
            tx,
            "UPDATE projects "
            "SET tags=?, world_baseline_json=NULL, narrative_pov=NULL, updated_at=? "
            "WHERE id=?",
            (_json.dumps([], ensure_ascii=False), iso_now(), project_id),
        )


def _safe_unlink(p: Path) -> None:
    """删文件,不存在时静默。"""
    try:
        if p.exists():
            p.unlink()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning("删除上传文件 %s 失败: %s", p, e)


# ======================================================================
# Sprint 3.A 末尾态:从项目最近 ready upload 抽末尾 N 字
# ======================================================================

class NoReadyUploadForTailExcerpt(Exception):
    """项目无 ready upload — 末尾态创建推演前置条件未达成。

    router 抛 422 END_MODE_NO_UPLOAD,前端 toast:"请先上传作品文件并完成 AI 抽图谱"。
    """


# 末尾态末段缓存默认长度:2000 字。
# 取舍考虑:
#   - 太短(<800)→ AI 接不上原作语境 / 笔法变形
#   - 太长(>3000)→ director user prompt 膨胀,LLM 注意力分散
#   - 2000 字 ≈ 1.3 个标准章节末段,够 AI 捕捉语体 + 物理状态 + 角色情绪
DEFAULT_TAIL_EXCERPT_CHARS = 2000


def _smart_cut_tail(text: str, max_chars: int) -> str:
    """从文本末尾取约 max_chars 字,智能边界(不切在句中)。

    策略:
      1. 长度 ≤ max_chars → 直接返回全文
      2. 否则取最后 max_chars 字 → 从前向后找第一个句号 / 段落分隔,
         从该点之后开始(保证开头是完整句)
      3. 若搜不到边界(前 30% 都无标点),硬切返回(罕见;古文 / 极长句)

    句号 / 段落分隔的优先级:
      段落分隔 \\n\\n > 中文句号 。/!/? > 西文句号 ./!/? > 换行 \\n

    Args:
      text: 原文(parse_file 输出)
      max_chars: 目标长度上限
    Returns:
      末段 str(可能比 max_chars 短,因为切到了边界)
    """
    if len(text) <= max_chars:
        return text

    slice_ = text[-max_chars:]
    # 搜索边界 — 优先级越高的越早返回(段落 > 句号 > 换行)
    boundaries = ["\n\n", "。", "!", "?", "!", "?", ".", "\n"]
    # 在 slice 的前 30% 找;超过 30% 的边界离末尾太近,切出来的"末段"长度不足
    search_limit = max_chars // 3

    best_pos = -1
    for bdr in boundaries:
        idx = slice_.find(bdr, 0, search_limit)
        if idx >= 0:
            # 切到该 boundary 之后(含 boundary 字符本身)
            best_pos = idx + len(bdr)
            break

    if best_pos < 0:
        # 全 slice 前 30% 都没标点 — 硬切
        return slice_.lstrip()
    return slice_[best_pos:].lstrip()


def get_project_tail_excerpt(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    max_chars: int = DEFAULT_TAIL_EXCERPT_CHARS,
) -> str:
    """取项目最近 ready upload 的末尾 N 字(末尾态续写 prior_context 用)。

    选 ready 而非 parsed/extracting,因为:
      - parsed 但未抽图谱:理论可用,但用户预期是"抽完图谱再续写"(产品流程)
      - extracting:正在处理,可能产生半成品
      - failed/rejected:文件本身有问题,不该当作 canon

    多个 ready upload(用户分批上传章节):取最近 uploaded_at(最后一次上传 = 用户当前关心的)。

    Raises:
      ResourceNotFoundOrForbidden     project 不属于该用户
      NoReadyUploadForTailExcerpt     项目无 ready upload(转 422 END_MODE_NO_UPLOAD)
    """
    get_project_or_403(conn, project_id, user_id)

    row = fetch_one(
        conn,
        "SELECT * FROM uploads WHERE project_id=? AND state='ready' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        raise NoReadyUploadForTailExcerpt(
            "项目还没有 ready 的作品文件 — 末尾态续写需先上传作品 + 完成 AI 抽图谱"
        )

    upload = Upload.from_row(row)
    abs_path = settings.uploads_abs_dir / upload.storage_path
    if not abs_path.exists():
        # 文件被外部清理但 DB 行还在 — 当作"无可用 upload"处理(不当 500)
        raise NoReadyUploadForTailExcerpt(
            f"作品文件物理路径丢失({upload.filename}),请重新上传"
        )

    parse_result = parse_file(abs_path, upload.mime_type)
    if not parse_result.success:
        raise NoReadyUploadForTailExcerpt(
            f"作品文件解析失败({upload.filename}):{parse_result.error}"
        )

    return _smart_cut_tail(parse_result.text, max_chars)


__all__ = [
    "UploadRequest",
    "UploadFormatRejected",
    "UploadDuplicate",
    "UploadParseFailed",
    "UploadRejectedRedFlag",
    "NoReadyUploadForTailExcerpt",
    "DEFAULT_TAIL_EXCERPT_CHARS",
    "receive_upload",
    "list_project_uploads",
    "get_upload_or_404",
    "delete_upload",
    "get_project_tail_excerpt",
]
