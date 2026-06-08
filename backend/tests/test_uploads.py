"""Upload 服务端到端测试 — Sprint 2.A 验收基线。

测试分组:
  A. 基础 API:.txt happy / .epub happy / .docx happy / 401 / 跨用户 404
  B. 格式拒:大小超限 / mime 非白名单 / 扩展名与 mime 不一致 / 空文件
  C. 红旗:命中 → 422 + violation_logs 落 + 文件不存 + 不落 uploads 行
  D. 去重:同 sha256 第 2 次 → 409 + 返回旧 id
  E. List + Delete:列表按时间倒序 + DELETE 真的删了文件

设计:
  - 用 io.BytesIO + python-docx / ebooklib 临时生成最小 .docx / .epub 内存文件
  - 上传成功后断 uploads 表行 + 磁盘文件存在
  - 测试间 reset_test_db 重建 DB,uploads 文件夹通过 settings 存在 backend/data/uploads(测试不清,但每次 user_id 是新 uuid 不会撞)
"""
from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import _connect, execute, transaction


TXT_MIME = "text/plain"
EPUB_MIME = "application/epub+zip"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


# ============================================================
# helpers
# ============================================================

def _create_project(client: TestClient, headers: dict, name: str = "中间态测试") -> str:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "tags": []},
    ).json()
    return p["id"]


def _seed_minimal_red_flags(test_db_path: Path) -> dict[str, str]:
    """灌少量真红旗词到测试 DB(避免依赖 seed JSON 文件;返回 {category: pattern})。"""
    import uuid
    from datetime import datetime, timezone

    conn = _connect(test_db_path)
    seeds = [
        ("political", "颠覆国家政权", 0, "block"),
        ("violence", "自制炸药", 0, "block"),
        ("sexual", "强奸幼女", 0, "block"),
        ("privacy", r"[1-9][0-9]{16}[0-9X]", 1, "block"),  # 18 位身份证
    ]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with transaction(conn) as tx:
            for cat, pat, is_re, sev in seeds:
                execute(
                    tx,
                    "INSERT INTO red_flag_dictionary "
                    "(id, category, pattern, is_regex, severity, enabled, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (str(uuid.uuid4()), cat, pat, is_re, sev, now),
                )
    finally:
        conn.close()
    return {cat: pat for cat, pat, _, _ in seeds}


def _make_minimal_docx_bytes(text: str = "测试段落一。\n第二段。") -> bytes:
    """python-docx 内存生成最小 .docx。"""
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for para in text.split("\n"):
        doc.add_paragraph(para)
    doc.save(buf)
    return buf.getvalue()


def _make_minimal_epub_bytes(text: str = "测试章节内容") -> bytes:
    """ebooklib 内存生成最小 .epub。"""
    import tempfile

    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-id")
    book.set_title("测试书")
    book.set_language("zh")
    book.add_author("测试作者")

    chapter = epub.EpubHtml(title="第一章", file_name="ch1.xhtml", lang="zh")
    chapter.content = f"<html><body><h1>第一章</h1><p>{text}</p></body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    # ebooklib 只支持写到磁盘文件路径,绕一下临时文件
    fd, tmp_path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    try:
        epub.write_epub(tmp_path, book)
        return Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _abs_storage_path(storage_relative: str) -> Path:
    """从 uploads.storage_path(uploads_abs_dir 相对)拼绝对磁盘路径。"""
    return settings.uploads_abs_dir / storage_relative


# ============================================================
# A. 基础 happy path
# ============================================================

def test_upload_txt_happy_path(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    content = "这是一段普通的小说文本,武侠风格,刀光剑影,血染长街。"
    files = {"file": ("江湖夜雨.txt", content.encode("utf-8"), TXT_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "parsed"
    assert body["filename"] == "江湖夜雨.txt"
    assert body["mime_type"] == TXT_MIME
    assert body["size_bytes"] == len(content.encode("utf-8"))
    assert body["parsed_text_chars"] == len(content)


def test_upload_docx_happy_path(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    raw = _make_minimal_docx_bytes("一个新故事的开头。\n第二段在这里。")
    files = {"file": ("我的小说.docx", raw, DOCX_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "parsed"
    assert body["parsed_text_chars"] > 0


def test_upload_epub_happy_path(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    raw = _make_minimal_epub_bytes("第一章的内容,主角登场。")
    files = {"file": ("书.epub", raw, EPUB_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "parsed"
    assert body["parsed_text_chars"] > 0


def test_upload_epub_octet_stream_accepted(client: TestClient, make_user):
    """根因回归(2026-05-28):Windows 浏览器上传 .epub 常给 application/octet-stream
    (系统没注册 epub MIME),旧逻辑严格卡 content_type 白名单 → 误拒正常 epub.
    修复后以扩展名为准,octet-stream 应放行并正常解析."""
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    raw = _make_minimal_epub_bytes("第一章的内容,主角登场。")
    # 模拟浏览器未识别 epub 类型(Windows 常见)
    files = {"file": ("书.epub", raw, "application/octet-stream")}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "parsed"
    assert body["parsed_text_chars"] > 0
    # 落库 mime 应被纠正为权威类型(不是 octet-stream)
    assert body["mime_type"] == EPUB_MIME


def test_upload_unauthenticated_returns_401(client: TestClient):
    files = {"file": ("a.txt", b"abc", TXT_MIME)}
    r = client.post("/api/projects/some-id/uploads", files=files)
    assert r.status_code == 401


def test_upload_cross_user_project_returns_404(client: TestClient, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    pid_alice = _create_project(client, alice["headers"])
    files = {"file": ("a.txt", b"abc", TXT_MIME)}
    r = client.post(f"/api/projects/{pid_alice}/uploads", headers=bob["headers"], files=files)
    assert r.status_code == 404


# ============================================================
# B. 格式拒
# ============================================================

def test_upload_too_large_returns_400(client: TestClient, make_user, monkeypatch):
    """临时把上限调到 10 字节让测试快速跑(Settings 是 frozen dataclass,
    用 dataclasses.replace 造新实例 + monkeypatch service 模块内的 settings 引用)。"""
    import dataclasses

    from app.services import upload_service

    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    new_settings = dataclasses.replace(upload_service.settings, upload_max_bytes=10)
    monkeypatch.setattr(upload_service, "settings", new_settings)

    files = {"file": ("big.txt", b"x" * 20, TXT_MIME)}
    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UPLOAD_FORMAT_REJECTED"


def test_upload_unsupported_mime_returns_400(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    files = {"file": ("doc.pdf", b"%PDF-1.4\n%fake", "application/pdf")}
    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UPLOAD_FORMAT_REJECTED"


def test_upload_extension_mime_mismatch_returns_400(client: TestClient, make_user):
    """扩展名 .txt 但 mime 声明 epub → 拒(防伪造)。"""
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    files = {"file": ("not_really.txt", b"\x00\x00\x00\x00", EPUB_MIME)}
    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 400


def test_upload_empty_file_returns_400(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    files = {"file": ("empty.txt", b"", TXT_MIME)}
    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 400


# ============================================================
# C. 红旗扫描
# ============================================================

def test_upload_red_flag_blocks_with_422_and_logs_violation(
    client: TestClient, make_user
):
    """灌测试用红旗词 → 上传含该词的内容 → 422 + violation_logs 落 + 文件没存。"""
    from pathlib import Path as _Path
    test_db = _Path(os.environ["HUIMENG_DB_PATH"])
    _seed_minimal_red_flags(test_db)

    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    bad_text = "前面正常铺垫…然后他说要颠覆国家政权…后面也正常"
    files = {"file": ("bad.txt", bad_text.encode("utf-8"), TXT_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "UPLOAD_RED_FLAG"
    assert detail["category"] == "political"
    assert "颠覆国家政权" in detail["matched_text"]

    # uploads 表无行
    conn = _connect(test_db)
    try:
        rows = conn.execute(
            "SELECT * FROM uploads WHERE user_id=?", (user["user_id"],)
        ).fetchall()
        assert len(rows) == 0
        # violation_logs 落了 1 条
        v = conn.execute(
            "SELECT * FROM violation_logs WHERE user_id=?", (user["user_id"],)
        ).fetchall()
        assert len(v) == 1
        assert "颠覆国家政权" in v[0]["matched_text"]
    finally:
        conn.close()


def test_upload_lenient_passes_violence_in_literature(
    client: TestClient, make_user
):
    """文学正常元素绝不拦 — 武侠流血场景应通过。"""
    from pathlib import Path as _Path
    test_db = _Path(os.environ["HUIMENG_DB_PATH"])
    _seed_minimal_red_flags(test_db)

    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    # 武侠流血、刀剑、死亡、战争、亲密等文学正常元素 — 一个都不能误杀
    literary_text = (
        "刀光过处,鲜血四溅,几个匪徒应声倒地。"
        "她紧紧抱住他,泪流满面,'你不能死!'"
        "枪声响起,战火在小镇蔓延。"
        "他妈的,这群混蛋。"
        "想起文革时期的那段批斗,他闭上了眼睛。"
    )
    files = {"file": ("literary.txt", literary_text.encode("utf-8"), TXT_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201, r.text   # 不拦
    assert r.json()["state"] == "parsed"


def test_upload_pii_regex_blocks_id_card_number(client: TestClient, make_user):
    """身份证号正则 PII 应被 block(假号 110101199001011230 18 位)。"""
    from pathlib import Path as _Path
    test_db = _Path(os.environ["HUIMENG_DB_PATH"])
    _seed_minimal_red_flags(test_db)

    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    pii_text = "他的身份证号是 110101199001011230,需要保密。"
    files = {"file": ("pii.txt", pii_text.encode("utf-8"), TXT_MIME)}

    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 422
    assert r.json()["detail"]["category"] == "privacy"


# ============================================================
# D. 去重 — Sprint 6.A2 M7.E(2026-05-20)新增同 project 单作品拦截
#    + 跨 project 同 sha256 仍走 UPLOAD_DUPLICATE
# ============================================================

def test_upload_duplicate_sha256_cross_project_returns_409(
    client: TestClient, make_user,
):
    """跨 project 同 user 上传同 sha256 → 409 UPLOAD_DUPLICATE(sha256 用户级去重)。

    Sprint 6.A2 M7.E 后:同 project 第二次上传走 PROJECT_ALREADY_HAS_UPLOAD,
    所以 UPLOAD_DUPLICATE 测试必须用跨 project 触发。
    """
    user = make_user("hero")
    h = user["headers"]
    pid1 = _create_project(client, h)
    pid2 = _create_project(client, h)

    content = b"this is the same content"
    files = {"file": ("first.txt", content, TXT_MIME)}
    r1 = client.post(f"/api/projects/{pid1}/uploads", headers=h, files=files)
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    # 跨 project 同 sha256(改文件名也要拒)
    files2 = {"file": ("second.txt", content, TXT_MIME)}
    r2 = client.post(f"/api/projects/{pid2}/uploads", headers=h, files=files2)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["code"] == "UPLOAD_DUPLICATE"
    assert detail["existing_upload_id"] == first_id


def test_upload_project_already_has_upload_returns_409(
    client: TestClient, make_user,
):
    """Sprint 6.A2 M7.E(2026-05-20):同 project 已有 upload 时第二次上传 → 409。

    产品定位"一个项目 = 一个作品世界",防止多作品角色 / 关系 / 世界观混合。
    用户想换作品 → 新建项目;想替换 → 先 DELETE 旧 upload 再上传。
    """
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    # 第一个作品上传 OK
    r1 = client.post(
        f"/api/projects/{pid}/uploads", headers=h,
        files={"file": ("first.txt", b"content 1 some text", TXT_MIME)},
    )
    assert r1.status_code == 201
    first_id = r1.json()["id"]
    first_name = r1.json()["filename"]

    # 第二个不同内容的作品上传 → 拒 409
    r2 = client.post(
        f"/api/projects/{pid}/uploads", headers=h,
        files={"file": ("second.txt", b"completely different content here", TXT_MIME)},
    )
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["code"] == "PROJECT_ALREADY_HAS_UPLOAD"
    assert detail["existing_upload_id"] == first_id
    assert detail["existing_filename"] == first_name
    assert "一个项目只能装一部作品" in detail["message"]


def test_upload_after_delete_succeeds(
    client: TestClient, make_user,
):
    """删除旧 upload 后,同 project 可以重新上传新作品(替换语义)。"""
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    r1 = client.post(
        f"/api/projects/{pid}/uploads", headers=h,
        files={"file": ("old.txt", b"old book content", TXT_MIME)},
    )
    assert r1.status_code == 201
    old_id = r1.json()["id"]

    # 删旧 upload
    r_del = client.delete(f"/api/uploads/{old_id}", headers=h)
    assert r_del.status_code == 204

    # 重新上传新作品 → 应该成功
    r2 = client.post(
        f"/api/projects/{pid}/uploads", headers=h,
        files={"file": ("new.txt", b"new book content entirely", TXT_MIME)},
    )
    assert r2.status_code == 201
    assert r2.json()["id"] != old_id


# ============================================================
# E. List + Delete
# ============================================================

def test_list_uploads_returns_project_uploads_in_order(
    client: TestClient, make_user
):
    """Sprint 6.A2 M7.E(2026-05-20)— 同 project 只允许 1 个 upload,
    本测试用 2 个 project 各 1 upload,验 list 端点只返当前 project 的 upload(free 档上限 2 项目)。
    """
    user = make_user("hero")
    h = user["headers"]
    # 2 个独立项目各 1 upload(用不同 name 防 UNIQUE 撞;受 free 档 projects_total=2 限)
    pids = [_create_project(client, h, name=f"测试项目 {i}") for i in range(2)]
    upload_ids = []
    for i, pid in enumerate(pids):
        files = {"file": (f"f{i}.txt", f"内容 {i} 是不同的".encode("utf-8"), TXT_MIME)}
        r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
        assert r.status_code == 201, r.text
        upload_ids.append(r.json()["id"])

    # 每个 project 只返各自的 upload(隔离)
    for pid, uid in zip(pids, upload_ids):
        r_list = client.get(f"/api/projects/{pid}/uploads", headers=h)
        assert r_list.status_code == 200
        body = r_list.json()
        assert len(body) == 1
        assert body[0]["id"] == uid
    # 倒序 — 最新的在前
    times = [u["uploaded_at"] for u in body]
    assert times == sorted(times, reverse=True)


def test_delete_upload_removes_db_row_and_disk_file(
    client: TestClient, make_user
):
    user = make_user("hero")
    h = user["headers"]
    pid = _create_project(client, h)

    files = {"file": ("to_delete.txt", b"will be deleted", TXT_MIME)}
    r = client.post(f"/api/projects/{pid}/uploads", headers=h, files=files)
    assert r.status_code == 201
    upload_id = r.json()["id"]

    # 拉详情拿 storage_path 算盘符 —— 我们用 settings.project_root 拼
    # 注:upload.to_response() 不暴露 storage_path,这里直接查 DB
    from pathlib import Path as _Path
    conn = _connect(_Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        row = conn.execute(
            "SELECT storage_path FROM uploads WHERE id=?", (upload_id,)
        ).fetchone()
        abs_path = _abs_storage_path(row["storage_path"])
        assert abs_path.exists(), f"上传文件应在磁盘上 {abs_path}"
    finally:
        conn.close()

    # DELETE
    r_del = client.delete(f"/api/uploads/{upload_id}", headers=h)
    assert r_del.status_code == 204

    # DB 行消失
    r_get = client.get(f"/api/uploads/{upload_id}", headers=h)
    assert r_get.status_code == 404
    # 磁盘文件消失
    assert not abs_path.exists()


def test_delete_upload_cross_user_returns_404(client: TestClient, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    pid = _create_project(client, alice["headers"])
    files = {"file": ("a.txt", b"alice content", TXT_MIME)}
    r = client.post(
        f"/api/projects/{pid}/uploads", headers=alice["headers"], files=files,
    )
    upload_id = r.json()["id"]
    r_del = client.delete(f"/api/uploads/{upload_id}", headers=bob["headers"])
    assert r_del.status_code == 404
