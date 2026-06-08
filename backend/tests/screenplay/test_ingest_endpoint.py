"""摄入 endpoint 端到端测试 — PR#3。

测试矩阵:
  1. 上传 .txt → 201 + 摘要 dict
  2. 上传后 GET /novels → 列表里有
  3. GET /novels/{id} → 详情(章节列表,无段落正文)
  4. GET /chapters/{id} → 段落列表
  5. 上传空文件 → 400
  6. 上传不支持的格式 → 400
  7. 文件名缺失 → 400
  8. 单 endpoint 上传 2 本不同书 → 数据隔离
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db(monkeypatch, tmp_path: Path):
    """每个 test 用独立 DB,避免污染。"""
    db_file = tmp_path / "test_screenplay.db"
    # 通过 monkeypatch settings 切 DB 路径
    from app.screenplay.config import settings
    # 阶段 7 兼容父平台 reset_test_db autouse:settings 是 frozen dataclass,不能 monkeypatch — 父平台已注入 HUIMENG_DB_PATH
    _ = (monkeypatch, db_file)
    # 重建表
    # 阶段 7:init_db 已删,父平台 migration runner 自动建表
    pass
    yield db_file


@pytest.fixture
def client(screenplay_client) -> TestClient:
    # 阶段 7:用 conftest 的 screenplay_client(带 Bearer JWT)
    return screenplay_client


def _fixture_bytes(name: str) -> bytes:
    return (Path(__file__).parent / "fixtures" / "novels" / name).read_bytes()


# ============================================================
# 上传 + CRUD
# ============================================================

def test_upload_txt_returns_summary(client):
    """上传中文小说 → 201 + 3 章摘要"""
    content = _fixture_bytes("sample_chinese.txt")
    r = client.post(
        "/api/screenplay/novels",
        files={"file": ("sample_chinese.txt", io.BytesIO(content), "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "麦田里的守望者"
    assert body["source_format"] == "txt"
    assert body["total_chapters"] == 3
    assert len(body["chapters"]) == 3
    # 每个章节摘要含 id / paragraph_count / char_count
    for ch in body["chapters"]:
        assert "id" in ch
        assert ch["paragraph_count"] > 0
        assert ch["char_count"] > 0


def test_list_novels_after_upload(client):
    """上传 + 列表"""
    content = _fixture_bytes("sample_chinese.txt")
    client.post(
        "/api/screenplay/novels",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
    )
    r = client.get("/api/screenplay/novels")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "麦田里的守望者"


def test_get_novel_returns_chapters(client):
    """详情应含章节列表"""
    content = _fixture_bytes("sample_chinese.txt")
    upload_resp = client.post(
        "/api/screenplay/novels",
        files={"file": ("t.txt", io.BytesIO(content), "text/plain")},
    )
    novel_id = upload_resp.json()["novel_id"]

    r = client.get(f"/api/screenplay/novels/{novel_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "麦田里的守望者"
    assert len(body["chapters"]) == 3
    # 详情不含段落正文,只含章节摘要
    for ch in body["chapters"]:
        assert "paragraph_count" in ch
        assert "text" not in ch


def test_get_chapter_paragraphs(client):
    """获取单章段落"""
    content = _fixture_bytes("sample_chinese.txt")
    upload_resp = client.post(
        "/api/screenplay/novels",
        files={"file": ("t.txt", io.BytesIO(content), "text/plain")},
    )
    chapter_id = upload_resp.json()["chapters"][0]["id"]

    r = client.get(f"/api/screenplay/chapters/{chapter_id}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["paragraphs"]) > 0
    # 段落含 index_in_chapter + text
    p = body["paragraphs"][0]
    assert p["index_in_chapter"] == 1
    assert isinstance(p["text"], str)
    assert len(p["text"]) > 0


# ============================================================
# 错误路径
# ============================================================

def test_upload_empty_file_returns_400(client):
    r = client.post(
        "/api/screenplay/novels",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PARSE_ERROR"


def test_upload_unsupported_format_returns_400(client):
    r = client.post(
        "/api/screenplay/novels",
        files={"file": ("novel.pdf", io.BytesIO(b"abc"), "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PARSE_ERROR"


def test_get_nonexistent_novel_returns_404(client):
    r = client.get("/api/screenplay/novels/nonexistent_id_xxx")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOVEL_NOT_FOUND"


def test_get_nonexistent_chapter_returns_404(client):
    r = client.get("/api/screenplay/chapters/nonexistent_id_xxx")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CHAPTER_NOT_FOUND"


# ============================================================
# 数据隔离
# ============================================================

def test_two_uploads_are_isolated(client):
    """上传 2 本不同书 → 数据互不污染"""
    r1 = client.post(
        "/api/screenplay/novels",
        files={"file": ("ch.txt", io.BytesIO(_fixture_bytes("sample_chinese.txt")), "text/plain")},
    )
    r2 = client.post(
        "/api/screenplay/novels",
        files={"file": ("en.txt", io.BytesIO(_fixture_bytes("sample_english.txt")), "text/plain")},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    r = client.get("/api/screenplay/novels")
    assert len(r.json()["items"]) == 2

    # 获取第 1 本的章节 ID,只属于第 1 本
    n1 = client.get(f"/api/screenplay/novels/{r1.json()['novel_id']}").json()
    n2 = client.get(f"/api/screenplay/novels/{r2.json()['novel_id']}").json()
    n1_ch_ids = {c["id"] for c in n1["chapters"]}
    n2_ch_ids = {c["id"] for c in n2["chapters"]}
    assert not (n1_ch_ids & n2_ch_ids)   # 章节 ID 完全不重叠
