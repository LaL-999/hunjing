"""自动图谱抽取服务端到端测试 — Sprint 2.B 验收基线。

测试分组:
  A. 触发 + 鉴权:happy / 401 / 跨用户 404 / upload 非 parsed 422 / 配额超限 429
  B. 主流程:LLM 输出落 characters / relationships / events + projects.type/tags 回填 + uploads.state=ready
  C. 重抽:is_admin_retag=1 + 项目内同名 character skip
  D. AI 输出非 4 类 type:走 generic + custom_type_name 兜底
  E. LLM 失败:state=failed + uploads.state=failed + error_message 落库
  F. List 端点:按时间倒序

设计:
  - patched_extract_funcs fixture 直接 monkeypatch extract_service 内 3 个 LLM 函数
  - sync_extract_runner monkeypatch kick_off_extract = run_extract 同步跑
  - 用真实场景数据(《江湖夜雨》),不用 test1/test2
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TXT_MIME = "text/plain"


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sync_extract_runner(monkeypatch):
    """把 extract_service.kick_off_extract 换成同步 run_extract 直跑。

    Sprint 6.A2 FOCUS.10(2026-05-22):新增 entities_pending_review 阶段会暂停 worker
    等用户审核。测试需绕过 — 同步跑完后若停在 review,**自动 approve(空增删 = 全部接受)**
    并续跑,等价于"老的一气呵成"行为,不影响测试断言。
    生产场景仍走真实审核流程(前端 modal + 用户点批准)。
    """
    import app.services.extract_service as svc

    def _patched_kick_off(job_id: str) -> None:
        svc.run_extract(job_id)
        # 跑完一轮 — 若停在 review 阶段,auto-approve 并续跑
        # 用 fresh connection 防止跨线程 / 跨 fixture 的 sqlite 连接复用问题
        conn = svc.get_connection()
        try:
            job = svc._fetch_job(conn, job_id)
            if job is not None and job.state == "entities_pending_review":
                svc.approve_entities(
                    conn, job_id=job_id, user_id=job.user_id,
                    added_persons=[], removed_names=[],
                )
                # approve_entities 内部会再调 kick_off_extract → 本函数 → 走 fast-path
        finally:
            conn.close()

    monkeypatch.setattr(svc, "kick_off_extract", _patched_kick_off)


@pytest.fixture
def patched_extract_funcs(monkeypatch):
    """注入 extract_service 内 3 个 LLM 函数的输出。

    用法:
        patched_extract_funcs.graph_output = {"entities": [...], "relations": [...]}
        patched_extract_funcs.profile_outputs = {"林晚": {...}}  # name → profile dict
        patched_extract_funcs.meta_output = {"type": "novel", "tags": [...]}
        patched_extract_funcs.usage = {"input_tokens": 100, "output_tokens": 50}
    """
    class Ctrl:
        graph_output: dict = {"entities": [], "relations": []}
        profile_outputs: dict = {}
        meta_output: dict = {"type": "novel", "custom_type_name": None, "tags": []}
        usage: dict = {"input_tokens": 100, "output_tokens": 50}
        graph_exception: Exception | None = None
        profile_exception: Exception | None = None
        meta_exception: Exception | None = None

    ctrl = Ctrl()

    def fake_extract_graph_chunked(
        text, work_name="", work_type="作品", chunk_chars=25000,
        on_chunk_done=None, on_chunk_failed=None,
        on_chunk_skipped=None, completed_chunks=None, should_cancel=None,
    ):
        if ctrl.graph_exception is not None:
            raise ctrl.graph_exception
        # cancel 协议:测试中 should_cancel 通常是 False,但若给了 ctrl.cancel_during_extract
        # 模拟"抽到一半被 reset"
        if should_cancel is not None and getattr(ctrl, "cancel_during_extract", False):
            raise InterruptedError("ctrl 模拟 cancel")
        completed_chunks = completed_chunks or {}
        total = max(1, 1 + len(completed_chunks))
        # 1) 触发已完成块的 skip 回调(resume 场景核心验证)
        if on_chunk_skipped is not None:
            for idx in sorted(completed_chunks.keys()):
                on_chunk_skipped(idx, total)
        # 2) 触发新抽 1 块的 done 回调(签名:idx, total, usage, hash, graph)
        new_chunk_idx = len(completed_chunks) + 1
        if on_chunk_done is not None:
            on_chunk_done(
                new_chunk_idx, total, ctrl.usage,
                f"fakehash_{new_chunk_idx}",
                ctrl.graph_output,
            )
        return ctrl.graph_output, ctrl.usage

    def fake_generate_character_profile(work_name, language_style, char_entity, source_text):
        if ctrl.profile_exception is not None:
            raise ctrl.profile_exception
        name = char_entity.get("name")
        return ctrl.profile_outputs.get(name, {}), ctrl.usage

    def fake_generate_minimal_profiles_batch(
        work_name, language_style, person_entities, source_text,
        excerpts_per_person_chars=1000,
    ):
        """批量轻量补全 fake — 默认每个角色返回带 personality / quotes / no_go_list 的精简档案,
        让测试能验证配角不再 identity-only。
        ctrl.minimal_batch_exception 可触发该批失败分支。
        """
        if getattr(ctrl, "minimal_batch_exception", None) is not None:
            raise ctrl.minimal_batch_exception
        out = []
        for ent in person_entities:
            name = ent.get("name", "")
            # 测试默认所有配角都补成功;ctrl.minimal_skip_names 控制 LLM 漏掉哪些
            if name in getattr(ctrl, "minimal_skip_names", set()):
                continue
            out.append({
                "name": name,
                "personality": f"配角 {name} 的轻量性格",
                "quotes": [f"{name} 说的话 1"],
                "no_go_list": [f"{name} 不会做的事 1"],
            })
        return out, ctrl.usage

    def fake_infer_meta(work_name, full_text):
        if ctrl.meta_exception is not None:
            raise ctrl.meta_exception
        return ctrl.meta_output, ctrl.usage

    monkeypatch.setattr(
        "app.services.extract_service.extract_graph_chunked", fake_extract_graph_chunked,
    )
    monkeypatch.setattr(
        "app.services.extract_service.generate_character_profile",
        fake_generate_character_profile,
    )
    monkeypatch.setattr(
        "app.services.extract_service.generate_minimal_profiles_batch",
        fake_generate_minimal_profiles_batch,
    )
    monkeypatch.setattr(
        "app.services.extract_service.infer_meta", fake_infer_meta,
    )
    return ctrl


# ============================================================
# helpers
# ============================================================

def _create_middle_project_with_upload(
    client: TestClient, headers: dict, content: bytes = None,
) -> tuple[str, str]:
    """造一个中间态项目 + 一个 parsed upload,返回 (project_id, upload_id)。"""
    if content is None:
        content = "江湖夜雨,客栈灯火摇曳。李寻欢与孙小红相对而坐,上官金虹推门而入。".encode("utf-8")
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "江湖夜雨", "type": "generic", "mode": "middle"},
    )
    assert p.status_code == 201, p.text
    project_id = p.json()["id"]

    files = {"file": ("江湖夜雨.txt", content, TXT_MIME)}
    u = client.post(f"/api/projects/{project_id}/uploads", headers=headers, files=files)
    assert u.status_code == 201, u.text
    upload_id = u.json()["id"]
    assert u.json()["state"] == "parsed"
    return project_id, upload_id


def _sample_extract_outputs(ctrl, char_names: list[str] | None = None):
    """给 ctrl 灌一组合理的 LLM 输出。"""
    if char_names is None:
        char_names = ["李寻欢", "孙小红", "上官金虹"]
    ctrl.graph_output = {
        "entities": [
            {"name": n, "type": "PERSON", "aliases": [], "description": f"{n} 的简介"}
            for n in char_names
        ] + [
            {"name": "客栈大堂", "type": "LOCATION", "aliases": [], "description": "夜雨客栈"},
            {"name": "推门相会", "type": "EVENT", "aliases": [], "description": "三人客栈夜会"},
        ],
        "relations": [
            {"source": char_names[0], "target": char_names[1], "type": "朋友", "description": "江湖故交"},
            {"source": char_names[0], "target": char_names[2], "type": "敌对", "description": "宿敌"},
            {"source": char_names[0], "target": "推门相会", "type": "参与", "description": ""},
            {"source": char_names[1], "target": "推门相会", "type": "参与", "description": ""},
        ],
    }
    ctrl.profile_outputs = {
        n: {
            "identity": f"{n} 的身份",
            "personality": f"{n} 的性格",
            "voice_fingerprint": {"quotes": [f"{n} 的台词 1", f"{n} 的台词 2"]},
            "no_go_list": [f"{n} 不会做的事"],
        } for n in char_names
    }
    ctrl.meta_output = {"type": "novel", "custom_type_name": None, "tags": ["武侠", "古风"]}


# ============================================================
# A. 触发 + 鉴权
# ============================================================

def test_extract_happy_path_falls_through_all_states(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    assert body["upload_id"] == upload_id
    assert body["project_id"] == project_id
    assert body["is_admin_retag"] is False
    assert body["characters_count"] == 3
    assert body["relationships_count"] == 2   # 朋友 + 敌对(过滤掉"参与"非人际)
    assert body["events_count"] == 1
    assert body["skipped_count"] == 0
    assert body["inferred_type"] == "novel"
    assert "武侠" in body["inferred_tags"]

    # uploads.state 推到 ready
    upload_after = client.get(f"/api/uploads/{upload_id}", headers=h).json()
    assert upload_after["state"] == "ready"

    # projects.type / tags 回填
    proj = client.get(f"/api/projects/{project_id}", headers=h).json()
    assert proj["type"] == "novel"
    assert "武侠" in proj["tags"]


def test_extract_unauthenticated_returns_401(client: TestClient):
    r = client.post("/api/uploads/x/extract")
    assert r.status_code == 401


def test_extract_cross_user_returns_404(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, upload_id = _create_middle_project_with_upload(client, alice["headers"])
    _sample_extract_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=bob["headers"])
    assert r.status_code == 404


def test_extract_upload_not_parsed_returns_422(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs, monkeypatch,
):
    """造一个 state='extracting' 的 upload(模拟已有进行中 job)→ 触发应被拒。"""
    from app.db import _connect, transaction, execute as db_execute
    import os

    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    # 直接改 uploads.state 模拟非 parsed
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    conn = _connect(test_db)
    try:
        with transaction(conn) as tx:
            db_execute(tx, "UPDATE uploads SET state='extracting' WHERE id=?", (upload_id,))
    finally:
        conn.close()

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UPLOAD_NOT_EXTRACTABLE"


# ============================================================
# B. 重抽
# ============================================================

def test_extract_retag_skips_existing_character_by_name(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
    monkeypatch,
):
    """第 1 次抽 → 落 3 character;手改第 1 个;第 2 次抽 → 同名 skip,只新增其它。
    用 founder 用户绕开 free 档 continuation 1/月 配额。"""
    import dataclasses
    user = make_user("hero")
    # 把当前测试用户邮箱加到 founder list,绕开配额限制
    from app import deps
    new_settings = dataclasses.replace(
        deps.settings,
        founder_emails=frozenset({user["email"].lower()}),
    )
    monkeypatch.setattr(deps, "settings", new_settings)

    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    _sample_extract_outputs(patched_extract_funcs, ["李寻欢", "孙小红", "上官金虹"])
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r1.status_code == 201
    assert r1.json()["state"] == "done"
    assert r1.json()["characters_count"] == 3

    # 手动把 uploads.state 改回 parsed 模拟重抽场景(实际中用户走 POST 重新触发)
    from app.db import _connect, transaction, execute as db_execute
    import os
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    conn = _connect(test_db)
    try:
        with transaction(conn) as tx:
            db_execute(tx, "UPDATE uploads SET state='parsed' WHERE id=?", (upload_id,))
    finally:
        conn.close()

    # 第 2 次:扩展角色集(原 3 + 1 新)→ 应 skip 旧 3 个,只插 1 新
    _sample_extract_outputs(patched_extract_funcs, ["李寻欢", "孙小红", "上官金虹", "阿飞"])
    r2 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["state"] == "done"
    assert body["is_admin_retag"] is True
    assert body["characters_count"] == 1     # 只新增 1 个
    assert body["skipped_count"] == 3        # skip 旧 3


def test_re_extract_from_ready_state_no_manual_workaround(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
    monkeypatch,
):
    """Sprint 6.A2 polish bug fix(2026-05-18):upload.state='ready' 时也允许重抽
    (用户在 ProjectUploadsPanel 点"✦ 重抽" 时,前端直接 POST /extract,不需要
    后端先手动把 state 回退到 'parsed')。

    对比上面 test_extract_retag_skips_existing_character_by_name — 它测的是
    "手动把 state 改回 parsed 后能抽",这条测的是"ready 状态直接 POST 也能抽"。

    验证 3 点:
      1. ready 状态 POST /extract 不返 422 UPLOAD_NOT_EXTRACTABLE
      2. 触发后 is_admin_retag=true
      3. 旧 extract_chunk_results 被清空(防 protagonist_judger / scene_extractor
         扫到旧版 LLM 输出污染派生数据)
    """
    import dataclasses
    user = make_user("re_extract_user")
    from app import deps
    new_settings = dataclasses.replace(
        deps.settings,
        founder_emails=frozenset({user["email"].lower()}),
    )
    monkeypatch.setattr(deps, "settings", new_settings)
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    # 第 1 次:upload 'parsed' → 抽完到 'ready'(_sample_extract_outputs 需要至少 3 角色)
    _sample_extract_outputs(patched_extract_funcs, ["李寻欢", "孙小红", "上官金虹"])
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r1.status_code == 201
    assert r1.json()["state"] == "done"

    # 验证 upload 已 'ready' 且 chunk_results 已写
    from app.db import _connect
    import os
    from pathlib import Path
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    conn = _connect(test_db)
    try:
        upload_row = conn.execute(
            "SELECT state FROM uploads WHERE id=?", (upload_id,)
        ).fetchone()
        assert upload_row["state"] == "ready"
        # 至少有旧的 chunk_results 行
        old_chunk_count = conn.execute(
            """SELECT COUNT(*) AS c FROM extract_chunk_results ecr
               JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
               WHERE gej.upload_id=?""",
            (upload_id,),
        ).fetchone()["c"]
        assert old_chunk_count > 0
    finally:
        conn.close()

    # 关键测试:ready 状态直接 POST /extract — 应**不报 422**
    _sample_extract_outputs(patched_extract_funcs, ["李寻欢", "孙小红", "上官金虹", "阿飞"])
    r2 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["state"] == "done"
    assert body["is_admin_retag"] is True

    # 验证旧 chunk_results 被清空(只剩新 job 的)
    conn = _connect(test_db)
    try:
        new_job_id = body["id"]
        # 所有剩余 chunk_results 都属于新 job
        remaining = conn.execute(
            """SELECT job_id FROM extract_chunk_results ecr
               JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
               WHERE gej.upload_id=?""",
            (upload_id,),
        ).fetchall()
        assert all(r["job_id"] == new_job_id for r in remaining), \
            "旧 chunk_results 应已清空,只留新 job 的"
    finally:
        conn.close()


def test_re_extract_rejected_while_extracting(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
    monkeypatch,
):
    """正在抽取中(state='extracting')仍拒绝触发新 job — 防并发同 upload 多 job。"""
    import dataclasses
    user = make_user("extracting_state")
    from app import deps
    new_settings = dataclasses.replace(
        deps.settings,
        founder_emails=frozenset({user["email"].lower()}),
    )
    monkeypatch.setattr(deps, "settings", new_settings)
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    # 手动把 upload state 改 'extracting'(模拟一个 in-flight job)
    from app.db import _connect, transaction, execute as db_execute
    import os
    from pathlib import Path
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    conn = _connect(test_db)
    try:
        with transaction(conn) as tx:
            db_execute(tx, "UPDATE uploads SET state='extracting' WHERE id=?", (upload_id,))
    finally:
        conn.close()

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UPLOAD_NOT_EXTRACTABLE"
    assert "抽取中" in r.json()["detail"]["message"]


# ============================================================
# C. AI 输出非 4 类 type → generic + custom_type_name
# ============================================================

def test_extract_ai_outputs_non_standard_type_falls_to_generic_with_custom(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """AI 输出 type='剧本杀' → schema 兜底为 generic + custom_type_name='剧本杀'。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    _sample_extract_outputs(patched_extract_funcs)
    patched_extract_funcs.meta_output = {
        "type": "剧本杀",   # 非 4 类
        "custom_type_name": None,
        "tags": ["悬疑", "推理"],
    }
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "done"
    assert body["inferred_type"] == "generic"
    assert body["inferred_custom_type_name"] == "剧本杀"

    # projects 回填也按兜底
    proj = client.get(f"/api/projects/{project_id}", headers=h).json()
    assert proj["type"] == "generic"
    assert proj["custom_type_name"] == "剧本杀"


# ============================================================
# D. LLM 失败
# ============================================================

def test_extract_llm_failure_marks_failed_and_failed_upload(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    patched_extract_funcs.graph_exception = RuntimeError("LLM call failed simulated")
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201, r.text   # POST 立即返;失败在后台落
    body = r.json()
    assert body["state"] == "failed"
    assert "LLM call failed simulated" in (body.get("error_message") or "")

    # uploads.state → failed
    upload_after = client.get(f"/api/uploads/{upload_id}", headers=h).json()
    assert upload_after["state"] == "failed"


# ============================================================
# E. 关系过滤(非人际关系不入 relationships 表)
# ============================================================

def test_extract_filters_non_interpersonal_relations(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """build_graph 输出的"参与"/"位于"/"拥有"/"提及" 不入 relationships 表。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    _sample_extract_outputs(patched_extract_funcs)
    # _sample 已含 4 条 relations(2 人际 + 2 "参与")— 应只入 2
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201
    assert r.json()["relationships_count"] == 2


# ============================================================
# F. List 端点
# ============================================================

def test_list_extract_jobs_returns_in_order(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
    monkeypatch,
):
    """跑 2 次抽取 — 用 founder 邮箱绕配额。"""
    import time
    import dataclasses
    user = make_user("hero")
    from app import deps
    new_settings = dataclasses.replace(
        deps.settings,
        founder_emails=frozenset({user["email"].lower()}),
    )
    monkeypatch.setattr(deps, "settings", new_settings)
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    _sample_extract_outputs(patched_extract_funcs)

    # 跑 1 次
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r1.status_code == 201
    time.sleep(0.01)

    # uploads.state 改回 parsed,再跑 1 次
    from app.db import _connect, transaction, execute as db_execute
    import os
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    conn = _connect(test_db)
    try:
        with transaction(conn) as tx:
            db_execute(tx, "UPDATE uploads SET state='parsed' WHERE id=?", (upload_id,))
    finally:
        conn.close()
    r2 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r2.status_code == 201

    # list 该项目
    r_list = client.get(f"/api/projects/{project_id}/extract_jobs", headers=h)
    assert r_list.status_code == 200
    body = r_list.json()
    assert len(body) == 2
    times = [j["started_at"] for j in body]
    assert times == sorted(times, reverse=True)


def test_get_extract_job_cross_user_returns_404(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, upload_id = _create_middle_project_with_upload(client, alice["headers"])
    _sample_extract_outputs(patched_extract_funcs)
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=alice["headers"])
    job_id = r.json()["id"]

    r_get = client.get(f"/api/extract_jobs/{job_id}", headers=bob["headers"])
    assert r_get.status_code == 404


# ============================================================
# G. 分块抽取 + 合并去重(unit 测试,不走 router)
# ============================================================

def test_split_text_into_chunks_short_text_returns_single_chunk():
    """≤ chunk_chars → 1 块。"""
    from app.services.llm_extract import split_text_into_chunks
    text = "短文本" * 100   # 300 字
    chunks = split_text_into_chunks(text, chunk_chars=25000)
    assert len(chunks) == 1


def test_split_text_into_chunks_long_text_splits_at_paragraph():
    """长文本 → 多块,优先在 \\n\\n 边界切。"""
    from app.services.llm_extract import split_text_into_chunks
    # 构造 30K 字文本,中间散落段落分隔
    paras = ["这是第 %d 段。" % i + "正文" * 100 for i in range(150)]
    text = "\n\n".join(paras)
    chunks = split_text_into_chunks(text, chunk_chars=10000)
    assert len(chunks) >= 2, f"30K 字应至少 2 块,实际 {len(chunks)}"
    # 每块都不超 chunk_chars(允许小幅超限因为段落对齐)
    for c in chunks:
        assert len(c) <= 12000, f"chunk {len(c)} 字超出 12000 上限"
    # 重新拼起来字数应接近原文(按 \n\n 切去掉空白后字数会少几个)
    rejoined_chars = sum(len(c) for c in chunks)
    assert abs(rejoined_chars - len(text)) < 500


def test_merge_graphs_dedups_entities_by_name():
    """多块输出含同名 entity → 合并 + aliases 取并集 + description 选最长。"""
    from app.services.llm_extract import _merge_graphs

    g1 = {
        "entities": [
            {"name": "贾宝玉", "type": "PERSON", "aliases": ["宝玉"], "description": "短描述"},
            {"name": "林黛玉", "type": "PERSON", "aliases": [], "description": "黛玉的简介"},
        ],
        "relations": [
            {"source": "贾宝玉", "target": "林黛玉", "type": "情侣", "description": "短"},
        ],
    }
    g2 = {
        "entities": [
            {"name": "贾宝玉", "type": "PERSON", "aliases": ["二爷", "宝玉"], "description": "更长更详细的描述"},
            {"name": "薛宝钗", "type": "PERSON", "aliases": [], "description": "薛家千金"},
        ],
        "relations": [
            {"source": "贾宝玉", "target": "林黛玉", "type": "情侣", "description": "更详细的描述"},
            {"source": "贾宝玉", "target": "薛宝钗", "type": "亲属", "description": "表亲"},
        ],
    }

    merged = _merge_graphs([g1, g2])

    # entities 去重(贾宝玉 1 + 林黛玉 1 + 薛宝钗 1 = 3)
    names = {e["name"] for e in merged["entities"]}
    assert names == {"贾宝玉", "林黛玉", "薛宝钗"}

    # 贾宝玉 aliases 并集(宝玉 + 二爷)
    baoyu = next(e for e in merged["entities"] if e["name"] == "贾宝玉")
    assert set(baoyu["aliases"]) == {"宝玉", "二爷"}
    # description 选更长的
    assert baoyu["description"] == "更长更详细的描述"

    # relations 去重((贾宝玉, 林黛玉, 情侣)只 1 条)
    assert len(merged["relations"]) == 2
    bao_lin = next(
        r for r in merged["relations"]
        if r["source"] == "贾宝玉" and r["target"] == "林黛玉"
    )
    assert bao_lin["description"] == "更详细的描述"


def test_humanize_error_translates_llm_json_failure():
    """LLM JSON 解析错误 → 转译成用户友好文案,不暴露 raw 200 字 / line column。"""
    from app.services.extract_service import _humanize_error
    raw = (
        "LlmJsonParseFailed: JSON 解析失败(含 repair 二次尝试):"
        "Expecting value: line 595 column 21 (char 11101);"
        "raw 前 200 字='{\\n \"entities\":...'"
    )
    msg = _humanize_error(raw)
    assert "AI 输出格式异常" in msg
    assert "LlmJsonParseFailed" not in msg
    assert "raw" not in msg
    assert "char" not in msg


def test_humanize_error_truncates_unknown_to_60_chars():
    from app.services.extract_service import _humanize_error
    raw = "x" * 200
    msg = _humanize_error(raw)
    assert len(msg) <= 61   # 60 + 省略号


# ============================================================
# H. 断点续抽(2.B+)— per-chunk 持久化 + reset + resume
# ============================================================


def _db_conn():
    """直接拿测试 db 连接(部分测试要 SQL 验证)。"""
    import os
    from app.db import _connect
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


def test_extract_persists_each_chunk_to_extract_chunk_results(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """每块抽完应立刻 INSERT extract_chunk_results — 这是断点续抽的命脉。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201
    job_id = r.json()["id"]

    # 验证 chunk_results 表里有该 job 的至少 1 行(fake 模拟 1 块新抽)
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT chunk_index, chunk_text_hash, graph_json, tokens_input, tokens_output "
            "FROM extract_chunk_results WHERE job_id=? ORDER BY chunk_index",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) >= 1
    assert rows[0]["chunk_text_hash"].startswith("fakehash_")
    assert rows[0]["tokens_input"] == 100
    assert rows[0]["tokens_output"] == 50


def test_reset_extract_job_marks_failed_and_keeps_chunks(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """reset 一个 done job 应被拒(409 EXTRACT_NOT_RESETTABLE)。
    然后造一个 extracting_graph 状态的 job → reset → state='failed' + upload='parsed' + chunks 保留。
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    # 跑一次到 done
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r1.status_code == 201
    job_id = r1.json()["id"]
    assert r1.json()["state"] == "done"

    # done 的 job reset → 409
    r_reset_done = client.post(f"/api/extract_jobs/{job_id}/reset", headers=h)
    assert r_reset_done.status_code == 409
    assert r_reset_done.json()["detail"]["code"] == "EXTRACT_NOT_RESETTABLE"

    # 手工把 job 改回 extracting_graph + upload 改 extracting,模拟僵尸态
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx,
                "UPDATE graph_extraction_jobs SET state='extracting_graph', "
                "completed_at=NULL WHERE id=?",
                (job_id,),
            )
            db_execute(tx, "UPDATE uploads SET state='extracting' WHERE id=?", (upload_id,))
    finally:
        conn.close()

    # reset → 200,state=failed,upload=parsed,chunks 仍在
    r_reset = client.post(f"/api/extract_jobs/{job_id}/reset", headers=h)
    assert r_reset.status_code == 200, r_reset.text
    assert r_reset.json()["state"] == "failed"
    assert "用户主动重置" in (r_reset.json()["error_message"] or "")
    assert r_reset.json()["resumable"] is True
    assert r_reset.json()["completed_chunks_count"] >= 1

    # upload 翻回 parsed
    upload_after = client.get(f"/api/uploads/{upload_id}", headers=h).json()
    assert upload_after["state"] == "parsed"

    # chunks 保留(给 resume 用)
    conn = _db_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM extract_chunk_results WHERE job_id=?",
            (job_id,),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n >= 1


def test_resume_extract_job_skips_completed_chunks_and_finishes(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """resume 应走 _load_completed_chunks → 跳过已存块 → 跑剩余 → done。

    通过预灌 chunk_results + 把 job 标 failed 来模拟"抽到一半被 reset"的状态。
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    # 1. 跑一次到 done(产生 1 个 job + 1 个 chunk_result)
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r1.status_code == 201
    job_id = r1.json()["id"]

    # 2. 模拟"backend 重启后变僵尸 + 用户 reset" → state=failed,chunks 保留
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx, "UPDATE graph_extraction_jobs SET state='failed', "
                "error_message='模拟 reset' WHERE id=?", (job_id,),
            )
            db_execute(tx, "UPDATE uploads SET state='parsed' WHERE id=?", (upload_id,))
    finally:
        conn.close()

    # 3. resume 端点:state=failed + 有 chunks → 应被允许
    r_resume = client.post(f"/api/extract_jobs/{job_id}/resume", headers=h)
    assert r_resume.status_code == 200, r_resume.text
    body = r_resume.json()
    # sync_extract_runner 是同步跑 → POST 返回时已 done
    assert body["state"] == "done"
    # 同 job_id 复用,不是新 job
    assert body["id"] == job_id
    # upload 推到 ready
    upload_after = client.get(f"/api/uploads/{upload_id}", headers=h).json()
    assert upload_after["state"] == "ready"


def test_resume_rejects_non_failed_job(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """resume 一个 done 的 job → 409 EXTRACT_NOT_RESUMABLE。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]
    assert r.json()["state"] == "done"

    r_resume = client.post(f"/api/extract_jobs/{job_id}/resume", headers=h)
    assert r_resume.status_code == 409
    assert r_resume.json()["detail"]["code"] == "EXTRACT_NOT_RESUMABLE"


def test_resume_rejects_failed_job_without_chunks(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """resume 一个 failed 但 chunks 表无数据的 job → 409 EXTRACT_NOT_RESUMABLE。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    # 让 graph 直接抛异常,job 直接进 failed,没机会写 chunk_results
    patched_extract_funcs.graph_exception = RuntimeError("LLM 模拟全 chunk 都失败")
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]
    assert r.json()["state"] == "failed"

    # resume 应被拒(没 chunk 可恢复)
    r_resume = client.post(f"/api/extract_jobs/{job_id}/resume", headers=h)
    assert r_resume.status_code == 409
    assert "无已完成块" in r_resume.json()["detail"]["message"]


def test_get_extract_job_attaches_resumable_and_completed_chunks_count(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """GET 端点应附带 is_alive / resumable / completed_chunks_count 字段。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    # 把 done 改成 failed 模拟"可 resume"
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx, "UPDATE graph_extraction_jobs SET state='failed' WHERE id=?",
                (job_id,),
            )
    finally:
        conn.close()

    r_get = client.get(f"/api/extract_jobs/{job_id}", headers=h)
    body = r_get.json()
    assert body["is_alive"] is False    # 同步测试模式,没活跃 thread
    assert body["resumable"] is True
    assert body["completed_chunks_count"] >= 1


def test_compute_chunk_hash_is_deterministic_and_changes_on_text_change():
    """chunk hash 必须 deterministic + 对内容敏感(用户换文件就 hash 变)。"""
    from app.services.llm_extract import compute_chunk_hash
    a = compute_chunk_hash("江湖夜雨")
    b = compute_chunk_hash("江湖夜雨")   # 同输入
    c = compute_chunk_hash("江湖晨雨")   # 改 1 字
    assert a == b
    assert a != c
    assert len(a) == 16   # sha256[:16]


def test_load_completed_chunks_skips_corrupt_json_rows(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """chunk_results 表里有损坏 JSON 行 → 加载时跳过(等于该块重抽),不阻塞 resume。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    # 注入 1 行损坏数据
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO extract_chunk_results "
                "(job_id, chunk_index, chunk_text_hash, graph_json, "
                " tokens_input, tokens_output, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, 99, "corrupt_hash", "{not valid json", 0, 0, "2026-01-01T00:00:00"),
            )
    finally:
        conn.close()

    # _load_completed_chunks 应跳过损坏行,只返合法的
    from app.db import _connect
    import os
    from app.services.extract_service import _load_completed_chunks
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        loaded = _load_completed_chunks(conn, job_id)
    finally:
        conn.close()
    # 99 号(损坏)不在结果里
    assert 99 not in loaded
    # 原来的 1 号在
    assert 1 in loaded


def test_reset_then_trigger_new_creates_separate_job(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
    monkeypatch,
):
    """reset 旧 job → 用户走 trigger 重新触发 → 应是全新 job_id,旧 chunks 不影响。
    用 founder 邮箱绕配额。"""
    import dataclasses
    user = make_user("hero")
    from app import deps
    new_settings = dataclasses.replace(
        deps.settings,
        founder_emails=frozenset({user["email"].lower()}),
    )
    monkeypatch.setattr(deps, "settings", new_settings)
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _sample_extract_outputs(patched_extract_funcs)

    # 1. 抽到 done
    r1 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id_1 = r1.json()["id"]

    # 2. 模拟僵尸态后 reset
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx,
                "UPDATE graph_extraction_jobs SET state='extracting_graph', "
                "completed_at=NULL WHERE id=?",
                (job_id_1,),
            )
            db_execute(tx, "UPDATE uploads SET state='extracting' WHERE id=?", (upload_id,))
    finally:
        conn.close()
    r_reset = client.post(f"/api/extract_jobs/{job_id_1}/reset", headers=h)
    assert r_reset.status_code == 200

    # 3. 用户走 trigger 全新抽取 → 新 job_id,扣新配额
    r2 = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r2.status_code == 201
    job_id_2 = r2.json()["id"]
    assert job_id_2 != job_id_1
    assert r2.json()["state"] == "done"
    # is_admin_retag = False:虽然 job_id_1 之前到过 done,但被手工改成 extracting_graph
    # 后又被 reset 改成 failed,prior_done 查询返空 → 视为首次抽取
    assert r2.json()["is_admin_retag"] is False


def test_reset_cross_user_returns_404(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """reset 别用户的 job → 404。"""
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, upload_id = _create_middle_project_with_upload(client, alice["headers"])
    _sample_extract_outputs(patched_extract_funcs)
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=alice["headers"])
    job_id = r.json()["id"]

    # 把 alice 的 job 改回 extracting_graph(本来是 done 不能 reset)
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx, "UPDATE graph_extraction_jobs SET state='extracting_graph' WHERE id=?",
                (job_id,),
            )
    finally:
        conn.close()

    r_bob_reset = client.post(f"/api/extract_jobs/{job_id}/reset", headers=bob["headers"])
    assert r_bob_reset.status_code == 404


def test_resume_cross_user_returns_404(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """resume 别用户的 job → 404。"""
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, upload_id = _create_middle_project_with_upload(client, alice["headers"])
    _sample_extract_outputs(patched_extract_funcs)
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=alice["headers"])
    job_id = r.json()["id"]

    # alice 的 job 改 failed(可 resume)
    conn = _db_conn()
    try:
        from app.db import transaction, execute as db_execute
        with transaction(conn) as tx:
            db_execute(
                tx, "UPDATE graph_extraction_jobs SET state='failed' WHERE id=?",
                (job_id,),
            )
    finally:
        conn.close()

    r_bob_resume = client.post(f"/api/extract_jobs/{job_id}/resume", headers=bob["headers"])
    assert r_bob_resume.status_code == 404


# ============================================================
# I. 批量轻量档案补全(2.B+ 方案 D)— top 30 之外的配角不再 identity-only
# ============================================================


def test_minimal_batch_fills_secondary_persons_with_personality(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """造 35 个 PERSON,top 30 走完整档案,剩 5 个走批量轻量补全 — 全部应有 personality。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    char_names = [f"角色{i:02d}" for i in range(1, 36)]   # 35 个
    _sample_extract_outputs(patched_extract_funcs, char_names)
    # _sample 默认只给 3 个 profile_outputs;补齐让 top 30 都有完整档案
    patched_extract_funcs.profile_outputs = {
        n: {
            "identity": f"{n} 的身份",
            "personality": f"{n} 的精品性格",
            "voice_fingerprint": {"quotes": [f"{n} 的台词 1"]},
            "no_go_list": [f"{n} 不会做的事"],
        }
        for n in char_names
    }
    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    # 35 个角色全部入库(2 个 LOCATION/EVENT 实体不算 character)
    assert body["characters_count"] == 35

    # 验证 db 里 35 个角色都有 personality(top 30 来自 profile_outputs / 后 5 来自 minimal batch)
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT name, length(personality) as p_len, length(quotes) as q_len "
            "FROM characters WHERE project_id=? ORDER BY name",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 35
    no_personality = [r["name"] for r in rows if r["p_len"] == 0]
    assert no_personality == [], f"预期所有角色都有 personality,实际有 {len(no_personality)} 个空"


def test_minimal_batch_failure_falls_back_to_identity_only(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """批量补全 LLM 全炸 → 该批配角降级 identity-only,不阻塞整体抽取。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    char_names = [f"角色{i:02d}" for i in range(1, 33)]   # 32 个 → top 30 + 2 个走批量
    _sample_extract_outputs(patched_extract_funcs, char_names)
    patched_extract_funcs.profile_outputs = {
        n: {
            "identity": f"{n} 的身份",
            "personality": f"{n} 的精品性格",
            "voice_fingerprint": {"quotes": [f"{n} 的台词 1"]},
            "no_go_list": [f"{n} 不会做的事"],
        }
        for n in char_names
    }
    # 批量补全炸了
    patched_extract_funcs.minimal_batch_exception = RuntimeError("批量 LLM 炸了模拟")

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201
    body = r.json()
    # 整体仍 done(批量失败不阻塞),32 角色都入库
    assert body["state"] == "done"
    assert body["characters_count"] == 32

    # top 30 有 personality;后 2 个降级 identity-only(personality 空)
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT name, length(personality) as p_len FROM characters "
            "WHERE project_id=? ORDER BY name",
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    no_personality_count = sum(1 for r in rows if r["p_len"] == 0)
    # 至少 1 个降级(具体几个看排序后哪些落入 top 30 之外)— 关键是不会全部都空
    assert no_personality_count <= 2, "降级不应超 2 个(只有走批量补全的才降级)"
    assert no_personality_count >= 1, "至少 1 个角色应走批量补全降级"


def test_minimal_batch_skipped_persons_get_identity_only(
    client: TestClient, make_user, sync_extract_runner, patched_extract_funcs,
):
    """LLM 漏返某角色 → 该角色降级 identity-only,其他批内角色照常。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)

    char_names = [f"角色{i:02d}" for i in range(1, 33)]
    _sample_extract_outputs(patched_extract_funcs, char_names)
    patched_extract_funcs.profile_outputs = {
        n: {
            "identity": f"{n} 的身份",
            "personality": f"{n} 的精品性格",
            "voice_fingerprint": {"quotes": [f"{n} 的台词"]},
            "no_go_list": [f"{n} 不会做的事"],
        }
        for n in char_names
    }
    # 批量补全 fake LLM 漏掉"角色31"(出场频次最低,大概率落入批)
    patched_extract_funcs.minimal_skip_names = {"角色31", "角色32"}

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201
    assert r.json()["state"] == "done"
    # 32 个角色仍全部入库(LLM 漏的也走第 3 层降级)
    assert r.json()["characters_count"] == 32
