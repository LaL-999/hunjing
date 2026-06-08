"""test_comic_service.py — Sprint D.9 Sprint 2.A 漫画态基础测试

测试范围:
  - CRUD:create / get / list / delete + source 校验
  - 状态机:cancel / is_comic_alive
  - Agent 路由 endpoints 基础(401 / 404 / 403 / state 校验);
    实际 agent LLM 调用走 mock 走主流程(LLM 真调用走 D.9 实测脚本,不在 pytest)
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient


# ============================================================
# ECON-1.4(2026-05-27 末⁴):漫创态全档清零 → 测试用 autouse fixture 临时还原
# ============================================================
# 本文件大量测试需要 POST /api/comics 创建漫画,ECON-1 后 comics_per_month=0
# 所有付费档都 429.本 fixture 临时还原旧配额(Pro=1 / Max=2 / Super Max=4),
# 让测试聚焦漫画功能本身,而非 quota 闸门.
# 例外:test_plan_limits_comics_per_month_values 测真实常量值(已自己 monkeypatch 回 0).
# 例外:test_create_comic_pro_plan_one_quota_then_blocked / test_create_comic_super_max_four_per_month
#       已显式 monkeypatch(autouse fixture 跑后它们再 patch,后者赢).
# ECON-2 漫画包 sprint 实现后,本 fixture 应改测"购买漫画包后能创建".


@pytest.fixture(autouse=True)
def _econ1_restore_comic_quota(request, monkeypatch):
    """ECON-1.4 临时还原 comics_per_month(测试 only),不影响生产配置."""
    # plan_limits 真值测试不能被改
    if "plan_limits_comics_per_month_values" in request.node.name:
        return

    from app.services.quota_service import PLAN_LIMITS, PlanLimits
    for plan, restored_comics in [("pro", 1), ("max", 2), ("super_max", 4)]:
        if plan not in PLAN_LIMITS:
            continue
        old = PLAN_LIMITS[plan]
        if old.comics_per_month >= restored_comics:
            continue  # 已被显式 monkeypatch,不动
        monkeypatch.setitem(PLAN_LIMITS, plan, PlanLimits(
            monthly_credits_quota=old.monthly_credits_quota,
            single_credit_price_cents=old.single_credit_price_cents,
            characters_per_project=old.characters_per_project,
            projects_total=old.projects_total,
            reshape_max_percent=old.reshape_max_percent,
            comics_per_month=restored_comics,
        ))


# ============================================================
# Helpers — 在 db 里直接造 "internal" 源依赖的 simulation
# ============================================================

def _seed_done_simulation(
    client: TestClient,
    user_headers: dict,
    user_id: str,
) -> str:
    """造一个 state='done' 的 simulation,供 comic source 引用。

    简化:直接 SQL insert(测试不走真实 simulation pipeline,只验证 comic 路由)。
    """
    from app.db import get_connection
    from app.services.comic_service import _new_comic_id, _now_iso

    conn = get_connection()
    try:
        sim_id = _new_comic_id()
        proj_id = _new_comic_id()
        now = _now_iso()

        # 先造一个 project(simulation 必须挂一个 project,FK 约束)
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, custom_type_name,
                                       tags, mode, created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, ?, 'novel', NULL, '[]', 'initial', ?, ?, '{}', 30)""",
            (proj_id, user_id, "测试项目", now, now),
        )

        # 造 simulation
        conn.execute(
            """INSERT INTO simulations (
                id, project_id, user_id, divergence,
                reshape_percent, rounds_planned, target_chars,
                style, custom_style_hint,
                context_simulation_ids, narrative_summary,
                characters_snapshot, state, current_round,
                timeline_json, narrative, tokens_input, tokens_output,
                cost_yuan, error_message, created_at,
                started_at, completed_at
            ) VALUES (?, ?, ?, '初始测试推演',
                       50, 10, 4000, 'A', NULL,
                       '[]', NULL,
                       ?, 'done', 10,
                       NULL, ?, 0, 0,
                       0.0, NULL, ?,
                       ?, ?)""",
            (
                sim_id, proj_id, user_id,
                json.dumps(
                    [{"id": "char1_id", "name": "测试主角"}], ensure_ascii=False
                ),
                "测试 narrative 内容用于漫画态;" * 50,   # ~1000 字
                now, now, now,
            ),
        )
        conn.commit()
        return sim_id
    finally:
        conn.close()


def _upgrade_to_max(user_id: str) -> None:
    """把测试用户升到 max 档 — 解 comics_per_month=2 配额(free 是 0,会被 enforce 挡)。

    Sprint 5.B(2026-05-18)起,POST /api/comics 接通 enforce_comic_count_quota,
    free 档 0 本配额会让所有 create 测试 429。所有需要 create 的 test 都先调此 helper。

    注:Sprint 5.B 之后,max 只能跑 2 本/月,所以单测试连续创建 ≥ 3 本时改用
    _upgrade_to_super_max(quota=4)或 _upgrade_to_founder(quota=999999)。
    """
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan='max' WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _upgrade_to_plan(user_id: str, plan: str) -> None:
    """Sprint 5.B 测试 helper:任意档位切换(test_enforce_comic_count_quota 系列用)。"""
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, user_id))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# CRUD 测试
# ============================================================

def test_create_comic_internal_source(client: TestClient, make_user):
    u = make_user("comic_creator")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "测试漫画 1",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "测试漫画 1"
    assert body["state"] == "queued"
    assert body["source"]["type"] == "internal"
    assert body["generation_seed"] is not None
    assert body["progress_percent"] == 0
    assert body["script"] is None   # Agent #2 没跑


def test_create_comic_invalid_source_empty(client: TestClient, make_user):
    u = make_user("comic_invalid")
    _upgrade_to_max(u["user_id"])
    # 缺 simulation_ids
    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={"name": "x", "source": {"type": "internal"}},
    )
    assert r.status_code == 422


def test_create_comic_invalid_source_other_user_sim(
    client: TestClient, make_user
):
    """跨用户引用别人的 simulation_id 应被拒。"""
    u1 = make_user("owner_a")
    u2 = make_user("invader_b")
    _upgrade_to_max(u2["user_id"])   # 受测的 u2 升档,免 429
    sim_id = _seed_done_simulation(client, u1["headers"], u1["user_id"])

    r = client.post(
        "/api/comics",
        headers=u2["headers"],
        json={
            "name": "盗用",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    assert r.status_code == 422
    assert "INVALID_COMIC_SOURCE" in r.text or "不属于" in r.text


def test_list_comics(client: TestClient, make_user):
    u = make_user("comic_lister")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    # 创建 2 个漫画
    for i in range(2):
        r = client.post(
            "/api/comics",
            headers=u["headers"],
            json={
                "name": f"漫画 {i}",
                "source": {"type": "internal", "simulation_ids": [sim_id]},
            },
        )
        assert r.status_code == 201

    r = client.get("/api/comics", headers=u["headers"])
    assert r.status_code == 200
    arr = r.json()
    assert len(arr) == 2


def test_get_comic_not_found(client: TestClient, make_user):
    u = make_user("comic_getter")
    r = client.get("/api/comics/nonexistent_id", headers=u["headers"])
    assert r.status_code == 404


def test_get_comic_403_other_user(client: TestClient, make_user):
    """跨用户访问别人的 comic 应 404(不暴露存在性)。"""
    u1 = make_user("owner_a2")
    u2 = make_user("invader_b2")
    _upgrade_to_max(u1["user_id"])
    sim_id = _seed_done_simulation(client, u1["headers"], u1["user_id"])
    r = client.post(
        "/api/comics",
        headers=u1["headers"],
        json={
            "name": "私密漫画",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    # u2 尝试访问
    r2 = client.get(f"/api/comics/{comic_id}", headers=u2["headers"])
    assert r2.status_code == 404


def test_delete_comic(client: TestClient, make_user):
    u = make_user("comic_deleter")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "待删",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    r2 = client.delete(f"/api/comics/{comic_id}", headers=u["headers"])
    assert r2.status_code == 200
    assert r2.json().get("deleted") is True

    # GET 404
    r3 = client.get(f"/api/comics/{comic_id}", headers=u["headers"])
    assert r3.status_code == 404


# ============================================================
# 状态机基础测试
# ============================================================

def test_cancel_queued_comic(client: TestClient, make_user):
    """queued 态 progress=0 → cancel 应"full" refund。"""
    u = make_user("comic_canceller")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "待取消",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    r2 = client.post(f"/api/comics/{comic_id}/cancel", headers=u["headers"])
    assert r2.status_code == 200
    body = r2.json()
    # Sprint C.2:响应结构 {comic, refund};refund.units = 真实 SUM(consume) 按 progress 退
    assert "comic" in body and "refund" in body
    assert body["comic"]["state"] == "cancelled"
    assert body["comic"]["completed_at"] is not None
    # progress=0 < 10 → 全退;但此 test comic 还没真消耗过 credit(state=queued 未跑 pipeline)
    # → total_consumed=0 → refund_units=0 / phase 仍是 'full'(规则:< 10% 全退,只是金额为 0)
    assert body["refund"]["phase"] == "full"
    assert body["refund"]["units"] == 0


def test_vote_style_wrong_state(client: TestClient, make_user):
    """state='queued' 时不能直接 vote_style,应 409。"""
    u = make_user("comic_vote_wrong")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "状态错乱",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    r2 = client.post(
        f"/api/comics/{comic_id}/vote_style",
        headers=u["headers"],
        json={"selected_index": 1},
    )
    assert r2.status_code == 409


def test_upload_references_validate_count(client: TestClient, make_user):
    """参考图必须 3 张,2 张应 422(Pydantic 校验)。"""
    u = make_user("comic_upload_2")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "上传不足",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    r2 = client.post(
        f"/api/comics/{comic_id}/upload_references",
        headers=u["headers"],
        json={"image_urls": ["http://a", "http://b"]},
    )
    assert r2.status_code == 422


# ============================================================
# Sprint 2.B+ 四修:cancel 退款规则单元测试
# ============================================================

def test_cancel_refund_half_when_progress_in_10_to_80(
    client: TestClient, make_user
):
    """progress=50 时 cancel → half refund(units=-50)。"""
    from app.db import get_connection

    u = make_user("comic_refund_half")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "半退测试",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    # 模拟 pipeline 已推进:state=style_analyzing,progress=50
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_projects SET state='style_analyzing', progress_percent=50 WHERE id=?",
            (comic_id,),
        )
        conn.commit()
    finally:
        conn.close()

    r2 = client.post(f"/api/comics/{comic_id}/cancel", headers=u["headers"])
    assert r2.status_code == 200
    body = r2.json()
    assert body["comic"]["state"] == "cancelled"
    assert body["refund"]["phase"] == "half"
    # Sprint C.2:total_consumed=0(无真实 consume)→ units=0 // 2=0;phase 仍 'half'
    # 真实 Sprint 3 跑通 pipeline 后,total_consumed > 0,half 退一半
    assert body["refund"]["units"] == 0


def test_cancel_refund_none_when_progress_ge_80(
    client: TestClient, make_user
):
    """progress=85 时 cancel → no refund(units=0,扣全本)。"""
    from app.db import get_connection

    u = make_user("comic_refund_none")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics",
        headers=u["headers"],
        json={
            "name": "不退测试",
            "source": {"type": "internal", "simulation_ids": [sim_id]},
        },
    )
    comic_id = r.json()["id"]

    # 模拟 pipeline 已快完成:state=designing,progress=85
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_projects SET state='designing', progress_percent=85 WHERE id=?",
            (comic_id,),
        )
        conn.commit()
    finally:
        conn.close()

    r2 = client.post(f"/api/comics/{comic_id}/cancel", headers=u["headers"])
    assert r2.status_code == 200
    body = r2.json()
    assert body["comic"]["state"] == "cancelled"
    assert body["refund"]["phase"] == "none"
    assert body["refund"]["units"] == 0


# Sprint C.1 老测试 `test_create_comic_free_plan_allowed_credit_check_at_llm` 已删除:
# 该测试断言"free 用户创建漫画 → 201",对应 C.1 时设计"漫画态不在 create 闸门"。
# Sprint 5.B(2026-05-18)漫画态降级反转该设计 → 漫画态走独立次数池 enforce_comic_count_quota,
# free 用户(comics_per_month=0)直接 429。新行为已由
# `test_create_comic_free_plan_blocked_by_zero_quota` 覆盖(本文件底部)。


# ============================================================
# 未鉴权
# ============================================================

def test_create_comic_unauthed(client: TestClient):
    r = client.post(
        "/api/comics",
        json={"name": "x", "source": {"type": "internal", "simulation_ids": []}},
    )
    assert r.status_code == 401


# ============================================================
# Sprint 4.C Typesetter 测试(2026-05-13)
#
# 重点验证:
#   - _agent_typesetter 在各种输入下都能产合法 PNG 文件 + 返回正确 URL
#   - _run_typesetter_all_pages 推进 state 到 done + 写 composed_url
#   - soft fail 哲学:单页失败不阻塞整本(写 panels_json=invalid 触发)
#
# 不测的:PIL 内部渲染像素准确性(视觉 QA 是 Phase 3 visual_qa 的事)
# ============================================================


class _MockComicForTypesetter:
    """最小 Comic mock — typesetter 只用到 .id 字段。"""
    def __init__(self, comic_id: str = "tset-test-001"):
        self.id = comic_id


def test_typesetter_produces_png_for_all_failed_panels(tmp_path, monkeypatch):
    """所有 panel image_url=None(零图)时仍产合法 PNG(失败占位 + dialogues/narrator/sfx)。"""
    from app.services import comic_service

    # 输出目录重定向到 pytest tmp,不污染 backend/data/composed/
    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )

    comic = _MockComicForTypesetter("tset-failed")
    panels = [
        {
            "panel_index": i + 1, "image_url": None,
            "dialogues": [{"speaker": "甲", "text": f"对白 {i + 1}"}],
            "narrator": f"旁白 {i + 1}",
            "sfx": ["啪"],
        }
        for i in range(6)
    ]
    url = comic_service._agent_typesetter(comic, 1, panels)

    assert url == "/api/comic-composed/tset-failed/page_1.png"
    output_file = tmp_path / "tset-failed" / "page_1.png"
    assert output_file.exists(), "PNG 文件未生成"
    assert output_file.stat().st_size > 1000, "PNG 文件太小可能无效"


def test_typesetter_empty_panels_list_still_succeeds(tmp_path, monkeypatch):
    """panels=[] 边缘 — 不抛错,仍产空白 PNG(orchestrator 兜底场景)。"""
    from app.services import comic_service

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )
    comic = _MockComicForTypesetter("tset-empty")
    url = comic_service._agent_typesetter(comic, 1, [])

    assert url == "/api/comic-composed/tset-empty/page_1.png"
    assert (tmp_path / "tset-empty" / "page_1.png").exists()


def test_typesetter_truncates_more_than_6_panels(tmp_path, monkeypatch):
    """LLM 偶尔产 > 6 格(防御);typesetter 截断到 6 格 不抛 IndexError。"""
    from app.services import comic_service

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )
    comic = _MockComicForTypesetter("tset-overflow")
    panels = [
        {
            "panel_index": i + 1, "image_url": None,
            "dialogues": [], "narrator": None, "sfx": [],
        }
        for i in range(10)  # 故意超 6
    ]
    url = comic_service._agent_typesetter(comic, 1, panels)
    assert (tmp_path / "tset-overflow" / "page_1.png").exists()


def test_typesetter_idempotent_overwrites(tmp_path, monkeypatch):
    """同 page_index 重跑 → 覆盖,不抛 FileExistsError(zombie 重启友好)。"""
    from app.services import comic_service

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )
    comic = _MockComicForTypesetter("tset-idem")
    panels = [
        {"panel_index": 1, "image_url": None, "dialogues": [],
         "narrator": "v1", "sfx": []},
    ]
    url1 = comic_service._agent_typesetter(comic, 1, panels)
    panels[0]["narrator"] = "v2 — 重跑后内容不同"
    url2 = comic_service._agent_typesetter(comic, 1, panels)
    assert url1 == url2  # URL 一致
    # 物理文件存在(被覆盖,size 可能变)
    assert (tmp_path / "tset-idem" / "page_1.png").exists()


def test_run_typesetter_all_pages_state_transition(client, make_user, tmp_path, monkeypatch):
    """集成测试:_run_typesetter_all_pages 把整本 generating → composing → done。

    verify:
      - comic.state 终态 = 'done'
      - comic.progress_percent = 100
      - 每个 comic_page.state = 'composed' + composed_url 非空
      - 物理 PNG 文件落盘
    """
    from app.db import get_connection
    from app.services import comic_service
    from app.models.comic import Comic

    u = make_user("tset_integration")
    _upgrade_to_max(u["user_id"])

    # 把输出目录重定向到 pytest tmp(避免污染 backend/data/composed/)
    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )

    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        # 造 comic — generating 末态(_generate_all_panels 已写完所有 comic_pages 行)
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'generating', 95, 1234, 2, ?, ?)""",
            (
                comic_id, u["user_id"], "排版集成测试",
                '{"type": "internal", "simulation_ids": []}',
                now, now,
            ),
        )
        # 造 2 个 page,各 3 个 image_url=None 的失败 panel
        for page_idx in (1, 2):
            conn.execute(
                """INSERT INTO comic_pages
                   (id, comic_id, page_index, panels_json, composed_url, state,
                    regenerated_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, 'generating', 0, ?, ?)""",
                (
                    comic_service._new_comic_id(), comic_id, page_idx,
                    json.dumps([
                        {"panel_index": i, "image_url": None,
                         "dialogues": [{"speaker": "甲", "text": "测试对白"}],
                         "narrator": None, "sfx": []}
                        for i in range(1, 4)
                    ], ensure_ascii=False),
                    now, now,
                ),
            )
        conn.commit()

        comic_row = conn.execute(
            "SELECT * FROM comic_projects WHERE id=?", (comic_id,)
        ).fetchone()
        comic = Comic.from_row(comic_row)

        # 跑 typesetter 主循环
        comic_service._run_typesetter_all_pages(conn, comic)

        # 校验 comic state
        row = conn.execute(
            "SELECT state, progress_percent FROM comic_projects WHERE id=?",
            (comic_id,),
        ).fetchone()
        assert row["state"] == "done"
        assert row["progress_percent"] == 100

        # 校验 comic_pages.composed_url 全部已写,state='composed'
        page_rows = conn.execute(
            "SELECT page_index, composed_url, state FROM comic_pages "
            "WHERE comic_id=? ORDER BY page_index ASC",
            (comic_id,),
        ).fetchall()
        assert len(page_rows) == 2
        for pr in page_rows:
            assert pr["composed_url"] is not None, f"page {pr['page_index']} composed_url 为空"
            assert pr["composed_url"].startswith(f"/api/comic-composed/{comic_id}/page_")
            assert pr["state"] == "composed"

        # 物理文件存在
        assert (tmp_path / comic_id / "page_1.png").exists()
        assert (tmp_path / comic_id / "page_2.png").exists()
    finally:
        conn.close()


def test_run_typesetter_all_pages_no_pages_still_done(client, make_user, tmp_path, monkeypatch):
    """边缘:comic_pages 表空(应该不可能,但兜底)— state 仍走到 done,不卡 composing。"""
    from app.db import get_connection
    from app.services import comic_service
    from app.models.comic import Comic

    u = make_user("tset_no_pages")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda comic_id: tmp_path / comic_id,
    )

    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'generating', 95, 1234, 2, ?, ?)""",
            (comic_id, u["user_id"], "空页测试",
             '{"type": "internal", "simulation_ids": []}', now, now),
        )
        conn.commit()
        comic = Comic.from_row(
            conn.execute("SELECT * FROM comic_projects WHERE id=?", (comic_id,)).fetchone()
        )

        comic_service._run_typesetter_all_pages(conn, comic)

        row = conn.execute(
            "SELECT state, progress_percent FROM comic_projects WHERE id=?",
            (comic_id,),
        ).fetchone()
        # 即使无 page,state 也走到 done(避免卡 composing 状态)
        assert row["state"] == "done"
        assert row["progress_percent"] == 100
    finally:
        conn.close()


# ============================================================
# Sprint 4.D 导出测试(2026-05-13)— PDF + PNG zip
# ============================================================


def _seed_done_comic_with_composed_pages(
    user_id: str, num_pages: int, tmp_path, fail_pages: set[int] | None = None
) -> str:
    """Helper:在 db 造一个 state='done' 的 comic + N 个 composed page。

    fail_pages:这些 page_index 的 composed_url 留 NULL(模拟 typesetter 部分失败)。
    composed PNG 文件在 tmp_path/composed/<comic_id>/page_<N>.png 真实生成。
    """
    from PIL import Image
    from app.db import get_connection
    from app.services import comic_service

    fail_pages = fail_pages or set()
    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'done', 100, 1234, ?, ?, ?)""",
            (
                comic_id, user_id, "导出测试漫画",
                '{"type": "internal", "simulation_ids": []}',
                num_pages, now, now,
            ),
        )
        composed_dir = tmp_path / "composed" / comic_id
        composed_dir.mkdir(parents=True, exist_ok=True)
        for p_idx in range(1, num_pages + 1):
            if p_idx in fail_pages:
                composed_url = None
            else:
                # 真实造一张 800x1500 单色 PNG(便于 PDF / ZIP 验证)
                img = Image.new("RGB", (800, 1500), color=(200 + p_idx % 50, 200, 200))
                img.save(composed_dir / f"page_{p_idx}.png", "PNG")
                composed_url = f"/api/comic-composed/{comic_id}/page_{p_idx}.png"
            conn.execute(
                """INSERT INTO comic_pages
                   (id, comic_id, page_index, panels_json, composed_url, state,
                    regenerated_count, created_at, updated_at)
                   VALUES (?, ?, ?, '[]', ?,
                           ?, 0, ?, ?)""",
                (
                    comic_service._new_comic_id(), comic_id, p_idx,
                    composed_url,
                    "composed" if composed_url else "failed",
                    now, now,
                ),
            )
        conn.commit()
        return comic_id
    finally:
        conn.close()


def test_export_pdf_returns_pdf_bytes(client: TestClient, make_user, tmp_path, monkeypatch):
    """正常路径:done 漫画 + 3 页 composed → GET /export.pdf 返回 PDF bytes。"""
    from app.services import comic_service

    u = make_user("export_pdf_ok")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )
    comic_id = _seed_done_comic_with_composed_pages(u["user_id"], 3, tmp_path)

    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=u["headers"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"   # PDF magic bytes
    # 文件名 header(含中文转 URL encode)
    assert "attachment" in r.headers["content-disposition"]
    # 无缺页时不写 X-Missing-Pages
    assert "x-missing-pages" not in (k.lower() for k in r.headers.keys())


def test_export_pdf_partial_missing_pages_header(
    client: TestClient, make_user, tmp_path, monkeypatch
):
    """部分页 composed_url=NULL → 仍返回 PDF + X-Missing-Pages header 列缺页号。"""
    from app.services import comic_service

    u = make_user("export_pdf_partial")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )
    # 5 页中 page 2 + 4 失败
    comic_id = _seed_done_comic_with_composed_pages(
        u["user_id"], 5, tmp_path, fail_pages={2, 4},
    )

    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=u["headers"])
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
    assert r.headers.get("X-Missing-Pages") == "2,4"


def test_export_pdf_all_pages_missing_returns_422(
    client: TestClient, make_user, tmp_path, monkeypatch
):
    """所有 composed_url 缺失 → 422 EXPORT_NOTHING_TO_DO。"""
    from app.services import comic_service

    u = make_user("export_pdf_empty")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )
    comic_id = _seed_done_comic_with_composed_pages(
        u["user_id"], 3, tmp_path, fail_pages={1, 2, 3},
    )

    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=u["headers"])
    assert r.status_code == 422, r.text
    assert "EXPORT_NOTHING_TO_DO" in r.text


def test_export_zip_returns_zip_with_pages(client: TestClient, make_user, tmp_path, monkeypatch):
    """正常路径:done 漫画 + 3 页 composed → GET /export.zip 返回 ZIP 含 3 个 PNG。"""
    import zipfile
    from io import BytesIO
    from app.services import comic_service

    u = make_user("export_zip_ok")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )
    comic_id = _seed_done_comic_with_composed_pages(u["user_id"], 3, tmp_path)

    r = client.get(f"/api/comics/{comic_id}/export.zip", headers=u["headers"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    z = zipfile.ZipFile(BytesIO(r.content))
    names = sorted(z.namelist())
    assert names == ["page_01.png", "page_02.png", "page_03.png"]
    # 每个 PNG 至少 100 字节
    for name in names:
        assert len(z.read(name)) > 100


def test_export_not_done_returns_422(client: TestClient, make_user, tmp_path):
    """state ≠ 'done' 的漫画 → 422 COMIC_NOT_EXPORTABLE。"""
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("export_not_done")
    _upgrade_to_max(u["user_id"])

    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'generating', 50, 1234, 6, ?, ?)""",
            (comic_id, u["user_id"], "未完成", '{"type": "internal", "simulation_ids": []}', now, now),
        )
        conn.commit()
    finally:
        conn.close()

    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=u["headers"])
    assert r.status_code == 422
    assert "COMIC_NOT_EXPORTABLE" in r.text

    r = client.get(f"/api/comics/{comic_id}/export.zip", headers=u["headers"])
    assert r.status_code == 422


def test_export_unauthed_returns_401(client: TestClient):
    """无 auth → 401。"""
    r = client.get("/api/comics/some-id/export.pdf")
    assert r.status_code == 401
    r = client.get("/api/comics/some-id/export.zip")
    assert r.status_code == 401


def test_export_other_users_comic_returns_404(client: TestClient, make_user, tmp_path, monkeypatch):
    """跨用户访问别人的漫画 → 404(不暴露存在性)。"""
    from app.services import comic_service

    owner = make_user("export_owner")
    _upgrade_to_max(owner["user_id"])
    invader = make_user("export_invader")
    _upgrade_to_max(invader["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )
    comic_id = _seed_done_comic_with_composed_pages(owner["user_id"], 2, tmp_path)

    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=invader["headers"])
    assert r.status_code == 404


# ============================================================
# Sprint 4.D — GET /api/uploads/ready
# ============================================================


def test_list_ready_uploads_returns_user_uploads_with_project_name(client: TestClient, make_user):
    """列出当前用户所有 state='ready' 的 uploads,带 project_name。"""
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("uploads_ready_user")
    _upgrade_to_max(u["user_id"])

    conn = get_connection()
    try:
        # 造 1 个 project + 2 个 uploads(1 ready / 1 parsed,只 ready 应被返回)
        proj_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, custom_type_name,
                                       tags, mode, created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, ?, 'novel', NULL, '[]', 'middle', ?, ?, '{}', 30)""",
            (proj_id, u["user_id"], "上传测试项目", now, now),
        )
        for i, (filename, state) in enumerate([("ready_book.txt", "ready"), ("not_ready.txt", "parsed")]):
            conn.execute(
                """INSERT INTO uploads
                   (id, project_id, user_id, filename, storage_path,
                    mime_type, size_bytes, sha256, parsed_text_chars, state,
                    error_message, uploaded_at)
                   VALUES (?, ?, ?, ?, ?,
                           'text/plain', 1024, ?, 5000, ?,
                           NULL, ?)""",
                (
                    comic_service._new_comic_id(), proj_id, u["user_id"],
                    filename, f"/fake/{filename}",
                    f"fakehash_{i:08x}",   # unique sha256 per row
                    state, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    r = client.get("/api/uploads/ready", headers=u["headers"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    # 仅 ready 被返回
    assert len(data) == 1
    assert data[0]["filename"] == "ready_book.txt"
    assert data[0]["project_name"] == "上传测试项目"
    assert data[0]["parsed_text_chars"] == 5000


def test_list_ready_uploads_empty(client: TestClient, make_user):
    """用户无 ready upload → 空列表(不是 404)。"""
    u = make_user("uploads_ready_empty")
    r = client.get("/api/uploads/ready", headers=u["headers"])
    assert r.status_code == 200
    assert r.json() == []


def test_list_ready_uploads_unauthed(client: TestClient):
    r = client.get("/api/uploads/ready")
    assert r.status_code == 401


def test_export_pdf_fallback_typesetter_for_legacy_comics(
    client: TestClient, make_user, tmp_path, monkeypatch,
):
    """Sprint 4.D+(2026-05-13):老漫画 composed_url=NULL 但 panels_json 有数据 →
    导出时 _ensure_page_composed 自动 on-the-fly 跑 typesetter,写回 db + 文件 → 导出成功。

    用户报告 bug:Sprint 4.C 接通前生成的漫画,导出失败"无可导出页面:所有
    composed_url 缺失或文件丢失"。Sprint 4.D+ fallback 修复:零迁移导出。
    """
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("export_fallback")
    _upgrade_to_max(u["user_id"])

    monkeypatch.setattr(
        comic_service, "_ts_composed_output_dir",
        lambda cid: tmp_path / "composed" / cid,
    )

    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'done', 100, 1234, 2, ?, ?)""",
            (
                comic_id, u["user_id"], "老漫画导出测试",
                '{"type": "internal", "simulation_ids": []}',
                now, now,
            ),
        )
        # 关键:2 个 page,composed_url=NULL,但 panels_json 含真实 panel(image_url=None
        # 仍可走 typesetter 失败占位路径,出整页 PNG)
        for p_idx in (1, 2):
            conn.execute(
                """INSERT INTO comic_pages
                   (id, comic_id, page_index, panels_json, composed_url, state,
                    regenerated_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, 'generating', 0, ?, ?)""",
                (
                    comic_service._new_comic_id(), comic_id, p_idx,
                    json.dumps([
                        {"panel_index": i, "image_url": None,
                         "dialogues": [{"speaker": "甲", "text": "测试"}],
                         "narrator": None, "sfx": []}
                        for i in range(1, 4)
                    ], ensure_ascii=False),
                    now, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # 跑导出 — fallback 应该自动 on-the-fly 生成 composed_url
    r = client.get(f"/api/comics/{comic_id}/export.pdf", headers=u["headers"])
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    # 全部 fallback 成功 → 无缺页
    missing_header = r.headers.get("X-Missing-Pages") or r.headers.get("x-missing-pages")
    assert not missing_header, f"应无缺页,实际:{missing_header}"

    # db 应已 fallback 写回 composed_url
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT page_index, composed_url, state FROM comic_pages "
            "WHERE comic_id=? ORDER BY page_index",
            (comic_id,),
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["composed_url"] is not None, (
                f"page {row['page_index']} composed_url 未写回"
            )
            assert row["state"] == "composed"
        # 物理 PNG 文件落盘
        assert (tmp_path / "composed" / comic_id / "page_1.png").exists()
        assert (tmp_path / "composed" / comic_id / "page_2.png").exists()
    finally:
        conn.close()


# ============================================================
# Sprint 5.8 工程债 #1 + #2 测试(2026-05-13)
# ============================================================


def test_extract_script_characters_freq_order():
    """_extract_script_characters 按出场频次降序、同频按名字升序去重。"""
    from app.services.comic_service import _extract_script_characters

    class _C:
        script = {"pages": [
            {"panels": [
                {"characters": ["张三", "李四"]},
                {"characters": ["张三", "王五"]},
            ]},
            {"panels": [
                {"characters": ["李四", "张三", "赵六"]},
            ]},
        ]}

    chars = _extract_script_characters(_C())
    # 张三 3 / 李四 2 / 王五 1 / 赵六 1(同频按名字升序)
    assert chars == ["张三", "李四", "王五", "赵六"]


def test_extract_script_characters_empty_or_missing_fields():
    """script 缺字段 / 不规范时不抛错,返回空列表。"""
    from app.services.comic_service import _extract_script_characters

    class _C1:
        script = None
    assert _extract_script_characters(_C1()) == []

    class _C2:
        script = {"pages": []}
    assert _extract_script_characters(_C2()) == []

    class _C3:
        script = {"pages": [{"panels": [{}, {"characters": None}]}]}
    assert _extract_script_characters(_C3()) == []

    class _C4:
        script = {"pages": [{"panels": [
            {"characters": ["", "  ", "甲"]},   # 空字符串过滤
        ]}]}
    assert _extract_script_characters(_C4()) == ["甲"]


def test_extract_character_context_grep_snippets():
    """从原文里抓角色名前后 80 字上下文。"""
    from app.services.comic_service import _extract_character_context_from_source

    src = "A" * 200 + "张三" + "B" * 200 + "中间内容" + "张三" + "C" * 200
    ctx = _extract_character_context_from_source(src, "张三", max_chars=2000)
    # 应包含 2 处出场 + 前后窗口
    assert ctx.count("张三") == 2
    assert "A" in ctx and "B" in ctx and "C" in ctx
    # 不应包含距离过远的 src 头尾
    assert len(ctx) < len(src)


def test_extract_character_context_no_match():
    """角色名不出现 → 空字符串。"""
    from app.services.comic_service import _extract_character_context_from_source

    assert _extract_character_context_from_source("一段无关原文", "张三") == ""
    assert _extract_character_context_from_source("", "张三") == ""
    assert _extract_character_context_from_source("xxx", "") == ""


def test_director_fingerprint_self_check_descriptor_present():
    """LLM 已逐字搬运 descriptor → 不需补救,返回空 list。"""
    from app.services.comic_service import _director_ensure_descriptors_injected

    descriptor = "17岁少年高二学生表面自卑懦弱但眼神深处藏着超乎年龄的冷静与思考力。眼型未明,瞳色未明。眉形微挑。"
    prompt = f"画风xxx {descriptor} 张三正在哭泣。禁止真人写实摄影,禁止文字水印。"
    chars = [{"name": "张三", "descriptor": descriptor}]
    result, injected = _director_ensure_descriptors_injected(prompt, chars)
    assert injected == []
    assert result == prompt   # 未修改


def test_director_fingerprint_self_check_force_inject_missing():
    """LLM 偷工减料只提了角色名 → fingerprint 命中失败 → 程序硬补完整 descriptor。"""
    from app.services.comic_service import _director_ensure_descriptors_injected

    descriptor = "17岁少年高二学生表面自卑懦弱但眼神深处藏着超乎年龄的冷静与思考力。眼型未明,瞳色未明。眉形微挑。"
    prompt_short = "画风xxx 张三正在哭泣。禁止真人写实摄影,禁止文字水印。"
    chars = [{"name": "张三", "descriptor": descriptor}]
    result, injected = _director_ensure_descriptors_injected(prompt_short, chars)
    assert injected == ["张三"]
    # 补回的完整 descriptor 应在结果里
    assert "17岁少年" in result
    assert "眼型未明" in result
    # 应插在 negative cue 最后一段("禁止"的最后出现位置)之前
    last_禁止_idx = result.rfind("禁止")
    desc_idx = result.find("17岁少年")
    assert desc_idx < last_禁止_idx, (
        f"补丁 descriptor 应插在 negative cue 之前,"
        f"实际 desc_idx={desc_idx} >= last_禁止_idx={last_禁止_idx}"
    )


def test_director_fingerprint_self_check_partial_multichar():
    """多角色 panel,LLM 完整提供 A 但缩写 B → 仅补 B。"""
    from app.services.comic_service import _director_ensure_descriptors_injected

    desc_a = "17岁少年高二学生表面自卑懦弱但眼神深处藏着超乎年龄的冷静与思考力。眼型未明,瞳色未明。眉形微挑。"
    desc_b = "20岁女青年,长发披肩,圆脸大眼,瓜子脸,白皙皮肤,常着浅蓝色连衣裙,踝带细链。"
    prompt = f"画风xxx {desc_a} 张三和李四对话。禁止真人写实摄影。"
    chars = [
        {"name": "张三", "descriptor": desc_a},
        {"name": "李四", "descriptor": desc_b},
    ]
    result, injected = _director_ensure_descriptors_injected(prompt, chars)
    assert injected == ["李四"]
    assert "20岁女青年" in result
    assert "17岁少年" in result   # 张三仍保留(本就在)


def test_director_fingerprint_self_check_no_descriptors_noop():
    """character_descriptors=[] 时不做事,返回原 prompt。"""
    from app.services.comic_service import _director_ensure_descriptors_injected

    prompt = "画风xxx 空教室。禁止真人写实摄影。"
    result, injected = _director_ensure_descriptors_injected(prompt, [])
    assert result == prompt
    assert injected == []


def test_director_fingerprint_self_check_short_descriptor_skipped():
    """descriptor < 30 字的不参与 fingerprint 校验(数据不全跳过)。"""
    from app.services.comic_service import _director_ensure_descriptors_injected

    prompt = "画风xxx 张三正在哭泣。禁止真人写实摄影。"
    chars = [{"name": "张三", "descriptor": "短描述"}]   # < 30 字
    result, injected = _director_ensure_descriptors_injected(prompt, chars)
    assert injected == []
    assert result == prompt


# ============================================================
# Sprint 5.1 并行生图测试(2026-05-13)
# ============================================================


def test_panel_parallel_workers_constant():
    """PANEL_PARALLEL_WORKERS 在合理范围(2-8),防误改成 1(回退串行)或 20(撞 QPS)。"""
    from app.services.comic_service import PANEL_PARALLEL_WORKERS
    assert 2 <= PANEL_PARALLEL_WORKERS <= 8


def test_process_single_panel_comic_not_found(client: TestClient, make_user):
    """worker 接收不存在的 comic_id → 优雅返回 error,不抛异常。"""
    from app.services.comic_service import _process_single_panel

    make_user("panel_404")   # 确保 db 存在
    panel_input = {
        "page_index": 1,
        "panel_index": 3,
        "characters": ["甲"],
        "dialogues": [{"speaker": "甲", "text": "测试"}],
        "narrator": None,
        "sfx": [],
        "shot_type": "中景",
    }
    panel_result, stats = _process_single_panel("non_existent_comic_id", panel_input)
    # panel_result 必须含完整 schema(供 panels_json 用)
    assert panel_result["panel_index"] == 3
    assert panel_result["image_url"] is None
    assert panel_result["dialogues"] == [{"speaker": "甲", "text": "测试"}]
    assert "error" in panel_result
    # stats 必须包含主线程汇总用字段
    assert stats["image_ok"] is False
    assert stats["director_input_tokens"] == 0
    assert stats["director_output_tokens"] == 0
    assert "comic_not_found" in stats["error"]


def test_process_single_panel_returns_clean_schema_on_director_failure(
    client: TestClient, make_user, monkeypatch,
):
    """director 抛错 → worker 兜底返 image_url=None + 干净 schema,不污染 panels_json。"""
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("panel_director_fail")
    _upgrade_to_max(u["user_id"])

    # 造个最小 comic(无 style_detailed_prompt → director 一调就抛 ValueError)
    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'designing', 80, 999, 6, ?, ?)""",
            (
                comic_id, u["user_id"], "director 失败测试",
                '{"type": "internal", "simulation_ids": []}',
                now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    panel_input = {
        "page_index": 1,
        "panel_index": 2,
        "characters": [],
        "dialogues": [{"speaker": "甲", "text": "x"}],
        "narrator": "旁白",
        "sfx": ["啪"],
        "shot_type": "近景",
    }
    panel_result, stats = comic_service._process_single_panel(comic_id, panel_input)
    # schema:director 失败时 image_url=None / prompt_used=None / 保留 dialogues 等
    assert panel_result["panel_index"] == 2
    assert panel_result["image_url"] is None
    assert panel_result["prompt_used"] is None
    assert panel_result["narrator"] == "旁白"
    assert panel_result["sfx"] == ["啪"]
    assert "error" in panel_result and "director:" in panel_result["error"]
    # 不应有任何 stats 内部字段污染 panels_json
    forbidden_keys = {"director_input_tokens", "director_output_tokens", "image_ok"}
    assert not (set(panel_result.keys()) & forbidden_keys)
    # stats 字段齐全
    assert stats["image_ok"] is False
    assert stats["director_input_tokens"] == 0


def test_process_single_panel_returns_panel_index_ordering_safe(
    client: TestClient, make_user,
):
    """worker 接收乱序 panel_input → 返回正确的 panel_index(_generate_all_panels 后续 sort 用)。

    并发完成顺序与 panel_index 无关 → 主线程必须靠 panel_result['panel_index'] 排序重建顺序。
    """
    from app.services.comic_service import _process_single_panel

    u = make_user("panel_idx_order")
    # 直接传 5 → 期望返回 panel_index=5(即使 comic 不存在,schema 也对)
    panel_input = {
        "page_index": 2,
        "panel_index": 5,
        "characters": [],
        "dialogues": [],
        "narrator": None,
        "sfx": [],
    }
    panel_result, _stats = _process_single_panel("bogus", panel_input)
    assert panel_result["panel_index"] == 5


# ============================================================
# Sprint 5.4 画风定调员 v3 测试(2026-05-13)
# ============================================================


def test_style_director_v3_prompt_file_exists_and_complete():
    """v3 prompt 文件存在 + 含 12 字段 + 内容解耦铁律 + 自适应 negative 规范。"""
    from app.services.comic_service import _load_prompt

    v3 = _load_prompt("style_director_v3.md")
    assert len(v3) > 3000   # 实际 ~6KB
    # v3 核心改进点都应在 prompt 里
    assert "v3 LOCKED" in v3
    # 12 字段新加的 4 个
    assert "mood" in v3
    assert "atmosphere" in v3
    assert "shadow_tone" in v3
    assert "inspirations" in v3
    # 内容-风格解耦铁律
    assert "内容-风格解耦" in v3 or "Content-Agnostic" in v3
    # 自适应 negative
    assert "negative_prompt" in v3
    # 候选差异化(非仅色温)
    assert "多维差异" in v3 or "差异化" in v3
    # 经典 v2 字段仍保留
    assert "brush_style" in v3
    assert "candidate_variants" in v3


def test_style_director_v2_archive_still_present():
    """Sprint 5.4 升级到 v3,但 v2 文件应保留作 archive(回退用)。"""
    from app.services.comic_service import _load_prompt

    v2 = _load_prompt("style_director_v2.md")
    assert "v2 LOCKED" in v2
    # 字段对比 — v2 应只有 8 字段(无 mood/atmosphere/shadow_tone/inspirations)
    assert "brush_style" in v2
    # v2 没有 mood 在 JSON 字段定义里(只在描述里可能出现)
    # 用更严格的判断:JSON 模板段不含 "mood"
    import re
    json_blocks = re.findall(r"```json\s*\n(.*?)\n```", v2, re.DOTALL)
    # v2 第一个 JSON block 是 Qwen-VL DNA 字段定义,不该含 mood
    if json_blocks:
        assert '"mood"' not in json_blocks[0], (
            "v2 Qwen-VL DNA JSON 不该含 mood(那是 v3 新加)"
        )


def _seed_external_upload(conn, user_id: str, project_id: str | None = None,
                           file_text: str = "测试源文本," * 80) -> str:
    """helper:为 external 源测试造真实落盘的 upload(file_parser 能 parse 出 text)。

    Sprint 4.D+ bug fix:uploads 表无 file_text 列,真实文本在磁盘
    settings.uploads_abs_dir / storage_path,_load_source_text_from_dict 调
    file_parser 重 parse。所以测试 fixture 必须真写文件到磁盘。
    """
    from app.config import settings
    from app.services.comic_service import _new_comic_id, _now_iso

    if project_id is None:
        project_id = _new_comic_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, custom_type_name,
                                       tags, mode, created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, ?, 'novel', NULL, '[]', 'middle', ?, ?, '{}', 30)""",
            (project_id, user_id, "测试项目", now, now),
        )
    else:
        now = _now_iso()
    upload_id = _new_comic_id()
    ext = ".txt"
    storage_path = f"{user_id}/{upload_id}{ext}"
    # 真写文件
    abs_path = settings.uploads_abs_dir / storage_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(file_text, encoding="utf-8")

    conn.execute(
        """INSERT INTO uploads
           (id, project_id, user_id, filename, storage_path,
            mime_type, size_bytes, sha256, parsed_text_chars, state,
            error_message, uploaded_at)
           VALUES (?, ?, ?, ?, ?, 'text/plain', ?,
                   ?, ?, 'ready', NULL, ?)""",
        (
            upload_id, project_id, user_id, "test.txt", storage_path,
            len(file_text.encode("utf-8")),
            f"sha_{upload_id[:16]}", len(file_text),
            now,
        ),
    )
    return upload_id


def test_load_source_text_from_dict_external_source(client: TestClient, make_user):
    """Sprint 4.D+ bug fix(2026-05-13)回归测试:
    _load_source_text_from_dict(source={type:external, upload_ids:[...]}) 不抛任何 SQL 错。
    双 bug 根因:
      A. 原 `ORDER BY created_at` → uploads 表只有 uploaded_at,SQLite 报 SQL 错
      B. 原读 `r["file_text"]` → uploads 表 schema 从未有此列,Sprint 2.A 写错
    修法:改用 storage_path + 实时 file_parser.parse_file。
    """
    from app.db import get_connection
    from app.services.comic_service import _load_source_text_from_dict

    u = make_user("source_load_external")
    _upgrade_to_max(u["user_id"])

    conn = get_connection()
    try:
        upload1 = _seed_external_upload(conn, u["user_id"],
                                          file_text="这是第一份文本内容,角色一" + "x" * 100)
        upload2 = _seed_external_upload(conn, u["user_id"],
                                          file_text="这是第二份文本内容,角色二" + "y" * 100)
        conn.commit()

        source_text = _load_source_text_from_dict(
            conn, {"type": "external", "upload_ids": [upload1, upload2]}
        )
        assert "第一份文本" in source_text
        assert "第二份文本" in source_text
        # 章节切分隔符存在(2 个 upload 拼起来)
        assert "章节切" in source_text
    finally:
        conn.close()


def test_plan_preview_external_source_no_500_bug(client: TestClient, make_user):
    """Sprint 4.D+ bug fix:plan_preview 对 external 源不应 500(用户实测痛点)。"""
    from app.db import get_connection

    u = make_user("plan_preview_external")
    _upgrade_to_max(u["user_id"])

    conn = get_connection()
    try:
        upload_id = _seed_external_upload(
            conn, u["user_id"],
            file_text="测试源文本," * 80,   # 400 字 > 100 安全
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post(
        "/api/comics/plan_preview",
        headers=u["headers"],
        json={
            "source": {"type": "external", "upload_ids": [upload_id]},
            "user_preference": "auto",
        },
    )
    # 关键断言:不该 500!即使 LLM 调用失败,后端也走兜底返回 200 + 12 页默认
    assert r.status_code != 500, (
        f"plan_preview external 源 500 回归 bug 再现!response={r.text}"
    )
    # 200 happy(LLM 失败时也走兜底 200);若 SOURCE_TOO_SHORT 测试 fixture 错
    assert r.status_code == 200, r.text
    data = r.json()
    assert "recommended_total_pages" in data
    assert 6 <= data["recommended_total_pages"] <= 18


def test_enforce_shot_diversity_first_wide_and_diversify():
    """Sprint 5.10:全近景 6 格 + 单角色 → 修首格远景 + 散开到 4 种 shot_type。"""
    from app.services.comic_service import _enforce_shot_diversity

    pages = [{"panels": [
        {"panel_index": i, "shot_type": "近景", "characters": ["甲"]}
        for i in range(1, 7)
    ]}]
    stats = _enforce_shot_diversity(pages)

    shots = [p["shot_type"] for p in pages[0]["panels"]]
    # 首格 wide
    assert shots[0] == "远景"
    assert stats["forced_first_wide"] == 1
    # 4 种以上多样性
    assert len(set(shots)) >= 4
    assert stats["forced_diversity"] >= 1


def test_enforce_shot_diversity_group_shot_for_multi_char():
    """Sprint 5.10:多角色页面无群像 → 强制把多角色那格改为群像。"""
    from app.services.comic_service import _enforce_shot_diversity

    pages = [{"panels": [
        {"panel_index": 1, "shot_type": "远景", "characters": []},
        {"panel_index": 2, "shot_type": "中景", "characters": ["张三", "李四", "王五"]},
        {"panel_index": 3, "shot_type": "近景", "characters": ["张三"]},
        {"panel_index": 4, "shot_type": "特写", "characters": ["李四"]},
    ]}]
    stats = _enforce_shot_diversity(pages)

    shots = [p["shot_type"] for p in pages[0]["panels"]]
    # 多角色那格(panel 2)被改为群像
    assert pages[0]["panels"][1]["shot_type"] == "群像"
    assert "群像" in shots
    assert stats["forced_group_shot"] == 1


def test_enforce_shot_diversity_skip_when_already_diverse():
    """Sprint 5.10:已满足首格 wide + 4 种多样性 + 有群像 → 不动。"""
    from app.services.comic_service import _enforce_shot_diversity

    pages = [{"panels": [
        {"panel_index": 1, "shot_type": "远景", "characters": []},
        {"panel_index": 2, "shot_type": "群像", "characters": ["甲", "乙"]},
        {"panel_index": 3, "shot_type": "中景", "characters": ["甲"]},
        {"panel_index": 4, "shot_type": "近景", "characters": ["乙"]},
        {"panel_index": 5, "shot_type": "特写", "characters": ["甲"]},
        {"panel_index": 6, "shot_type": "近景", "characters": ["乙"]},
    ]}]
    before = [p["shot_type"] for p in pages[0]["panels"]]
    stats = _enforce_shot_diversity(pages)
    after = [p["shot_type"] for p in pages[0]["panels"]]

    assert before == after
    assert stats["forced_first_wide"] == 0
    assert stats["forced_group_shot"] == 0
    assert stats["forced_diversity"] == 0


def test_enforce_shot_diversity_empty_or_malformed():
    """Sprint 5.10:输入异常(None / 空 / 缺 panels)不抛错。"""
    from app.services.comic_service import _enforce_shot_diversity

    assert _enforce_shot_diversity([])["pages"] == 0
    assert _enforce_shot_diversity([{}])["pages"] == 0
    assert _enforce_shot_diversity([{"panels": []}])["pages"] == 0
    # 不抛错就行


def test_director_shot_type_injected_when_missing():
    """Sprint 5.10:director prompt 不含 shot_type 英文关键词 → 头部硬注入。"""
    from app.services.comic_service import _director_ensure_shot_type_injected

    prompt = "画风测试 张三正在哭泣 禁止真人写实"
    new_prompt, injected = _director_ensure_shot_type_injected(prompt, "远景")
    assert injected
    assert new_prompt.startswith("[CAMERA: wide shot")
    assert "张三正在哭泣" in new_prompt


def test_director_shot_type_skipped_when_present():
    """Sprint 5.10:prompt 已含主关键词 → 不重复注入。"""
    from app.services.comic_service import _director_ensure_shot_type_injected

    prompt = "画风 wide shot, full body, 张三走在街上"
    new_prompt, injected = _director_ensure_shot_type_injected(prompt, "远景")
    assert not injected
    assert new_prompt == prompt


def test_director_shot_type_skipped_when_no_shot():
    """Sprint 5.10:shot_type 为 None / 未知 → 不注入。"""
    from app.services.comic_service import _director_ensure_shot_type_injected

    prompt = "原 prompt"
    n1, i1 = _director_ensure_shot_type_injected(prompt, None)
    n2, i2 = _director_ensure_shot_type_injected(prompt, "")
    n3, i3 = _director_ensure_shot_type_injected(prompt, "不存在的镜头")
    assert not i1 and n1 == prompt
    assert not i2 and n2 == prompt
    assert not i3 and n3 == prompt


def test_extract_json_block_basic():
    """Sprint 5.x bug fix:括号匹配从含 markdown 标题的输出里挖 JSON 对象。"""
    from app.services.llm_client import _extract_json_block

    # Case A: 标准 — 第一个 { 到对应 }
    assert _extract_json_block('{"a": 1}') == '{"a": 1}'

    # Case B: 前面有 markdown 标题(用户实测痛点)
    raw = """# 阶段 1 输出:visual_dnas 为空

---

# 阶段 2 输出:DeepSeek V3 综合详细画风 prompt

```json
{
  "style_detailed_prompt": "测试 prompt",
  "style_tag": "诡异"
}
```
"""
    block = _extract_json_block(raw)
    assert block is not None
    assert '"style_detailed_prompt"' in block
    assert block.endswith("}")
    # 应该能解析
    import json as _json
    parsed = _json.loads(block)
    assert parsed["style_tag"] == "诡异"


def test_extract_json_block_nested():
    """嵌套 JSON 对象 — 括号匹配必须深度正确。"""
    from app.services.llm_client import _extract_json_block

    raw = '{"a": {"b": {"c": 1}}, "d": [1, 2]}'
    block = _extract_json_block(raw)
    assert block == raw  # 整个就是一个对象


def test_extract_json_block_string_with_braces():
    """字符串里的 `{` `}` 不算结构性括号(转义 + 字符串内容)。"""
    from app.services.llm_client import _extract_json_block

    # JSON 里字符串值含 `{` `}`
    raw = '前缀文字 {"text": "包含 { 和 } 的字符串", "n": 42}'
    block = _extract_json_block(raw)
    import json as _json
    parsed = _json.loads(block)
    assert parsed["text"] == "包含 { 和 } 的字符串"


def test_extract_json_block_no_json_returns_none():
    """完全没有 JSON 块 → 返回 None。"""
    from app.services.llm_client import _extract_json_block

    assert _extract_json_block("纯文字没有 JSON") is None
    assert _extract_json_block("") is None
    assert _extract_json_block("{未闭合") is None  # 不平衡


def test_openai_compat_call_json_3stage_fallback(monkeypatch):
    """Sprint 5.x bug fix:LLM 输出含 markdown 标题 + 内嵌 JSON,3 级 fallback 能解出。"""
    from app.services import llm_client

    # 模拟 LLM 返回(用户实测痛点的真实输出格式)
    long_text = "【2D 半厚涂日漫惊悚风格】" + "整体氛围诡异压抑" * 20
    fake_raw = (
        "# 阶段 1 输出:visual_dnas 为空(text_only 模式,跳过)\n\n"
        "---\n\n"
        "# 阶段 2 输出:DeepSeek V3 综合详细画风 prompt\n\n"
        "```json\n"
        "{\n"
        f'  "style_detailed_prompt": "{long_text}",\n'
        '  "style_tag": "诡异暗黑日漫",\n'
        '  "negative_prompt": "禁止鲜艳明亮",\n'
        '  "candidate_variants": ["v1", "v2", "v3"]\n'
        "}\n"
        "```\n"
    )

    from types import SimpleNamespace
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=fake_raw),
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
    )

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return fake_resp

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kw: FakeClient(),
    )

    parsed, usage = llm_client._openai_compat_call_json(
        system_prompt="test",
        user_input={"x": 1},
        api_key="sk-test",
        api_base="https://test.example/v1",
        model="test-model",
        vendor_label="Test",
        retries=0,
    )
    # 关键断言:即使 LLM 输出含 markdown 标题前缀,也能挖出 JSON
    assert parsed["style_tag"] == "诡异暗黑日漫"
    assert len(parsed["candidate_variants"]) == 3


def test_screenwriter_v3_prompt_has_shot_diversity_rules():
    """Sprint 5.7:screenwriter v3 prompt 含镜头多样性 + emotion + head_anchor 必填铁律。"""
    from app.services.comic_service import _load_prompt

    p = _load_prompt("screenwriter.md")
    assert "v3" in p
    # 镜头多样性硬铁律
    assert "镜头多样性硬铁律" in p
    assert "≥ 4 种不同 shot_type" in p
    # 首格 wide
    assert "首格强制 wide_shot" in p or "首格" in p
    # 群像
    assert "群像" in p
    # emotion 字段
    assert "emotion" in p
    assert "anger" in p and "sadness" in p
    # head_anchor 字段
    assert "head_anchor" in p
    assert "left" in p and "right" in p
    # 12 铁律(v2 是 8)
    assert "12 铁律" in p


def test_director_v3_prompt_has_emotion_translation():
    """Sprint 5.7:director v3 prompt 含 emotion 翻译为身体语言指南。"""
    from app.services.comic_service import _load_prompt

    p = _load_prompt("director_v2.md")
    # Sprint 5.7 v3 标识
    assert "v3" in p or "Sprint 5.7" in p
    # emotion 翻译表
    assert "emotion" in p
    assert "anger" in p
    assert "tension" in p   # v3 加的
    # 群像 / 过肩 镜头新加
    assert "群像" in p
    assert "过肩" in p


def test_director_injects_emotion_field(client: TestClient, make_user, monkeypatch):
    """_agent_director user_input 应含 panel.emotion(scripter v3 出的字段流到 director)。"""
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("director_emotion_flow")
    _upgrade_to_max(u["user_id"])

    # mock LLM 调用,只验证 user_input 含 emotion
    captured: dict = {}

    def fake_call(*args, **kwargs):
        # capture user_input(第二个参数 OR 关键字)
        captured["user_input"] = kwargs.get("user_input") or (args[1] if len(args) > 1 else None)
        return "画风xxx 测试 prompt 内容 充足 200 字以上" + "x" * 200, {"input_tokens": 100, "output_tokens": 100}

    # Director 内部 `from app.services.llm_client import _openai_compat_call_text`,
    # 必须 patch 真实模块,不是 comic_service 的 namespace
    monkeypatch.setattr(
        "app.services.llm_client._openai_compat_call_text",
        fake_call,
    )

    conn = get_connection()
    try:
        comic_id = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, style_detailed_prompt,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, 'designing', 85, 111, 6, ?, ?, ?)""",
            (
                comic_id, u["user_id"], "x",
                '{"type": "internal", "simulation_ids": []}',
                "画风测试 anchor" + "x" * 200,   # ≥ 100 字
                now, now,
            ),
        )
        conn.commit()
        from app.models.comic import Comic
        comic = Comic.from_row(conn.execute(
            "SELECT * FROM comic_projects WHERE id=?", (comic_id,),
        ).fetchone())

        # Sprint 5.7 panel 含 emotion 字段
        panel_dict = {
            "page_index": 1, "panel_index": 1,
            "shot_type": "近景", "scene": None,
            "characters": [],
            "action": "测试",
            "dialogues": [],
            "narrator": None,
            "emotion": "fear",   # 关键测试字段
        }
        comic_service._agent_director(comic, conn, panel_dict)

        # 验证 user_input 含 emotion
        ui = captured["user_input"]
        import json as _json
        if isinstance(ui, str):
            ui = _json.loads(ui)
        assert ui["panel"]["emotion"] == "fear", (
            f"director 必须把 panel.emotion 传给 LLM,实际 user_input.panel={ui.get('panel')!r}"
        )
    finally:
        conn.close()


def test_update_progress_helper(client: TestClient, make_user):
    """Sprint 5.x bug fix:_update_progress 只更新 progress 不动 state。"""
    from app.db import get_connection
    from app.services import comic_service

    u = make_user("update_progress_test")
    _upgrade_to_max(u["user_id"])

    conn = get_connection()
    try:
        cid = comic_service._new_comic_id()
        now = comic_service._now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'style_analyzing', 50, 111, 6, ?, ?)""",
            (cid, u["user_id"], "x", '{"type": "internal", "simulation_ids": []}', now, now),
        )
        conn.commit()

        # 单纯更新 progress
        comic_service._update_progress(conn, cid, 55)
        row = conn.execute(
            "SELECT state, progress_percent FROM comic_projects WHERE id=?",
            (cid,),
        ).fetchone()
        assert row["state"] == "style_analyzing"   # state 保留
        assert row["progress_percent"] == 55

        # clamp 到 [0, 100]
        comic_service._update_progress(conn, cid, 250)
        row = conn.execute("SELECT progress_percent FROM comic_projects WHERE id=?", (cid,)).fetchone()
        assert row["progress_percent"] == 100

        comic_service._update_progress(conn, cid, -10)
        row = conn.execute("SELECT progress_percent FROM comic_projects WHERE id=?", (cid,)).fetchone()
        assert row["progress_percent"] == 0
    finally:
        conn.close()


def test_image_adapter_retries_on_transient_error(monkeypatch):
    """Sprint 5.x bug fix:adapter 对 429 / Timeout 等瞬态错重试 2 次,fatal 错(401)立刻抛。"""
    from app.services.llm_routing.adapters.jimeng_image import JimengImageAdapter
    from app.services.llm_routing.protocols import LlmCallFailed

    adapter = JimengImageAdapter()

    # Case A: 一直 429,重试 2 次后 raise(总 3 次尝试)
    call_count = {"n": 0}

    class FakeRateLimit(Exception):
        def __init__(self):
            super().__init__("429 Rate Limit exceeded")

    def fake_generate_429(*args, **kwargs):
        call_count["n"] += 1
        raise FakeRateLimit()

    class FakeClient:
        class images:
            generate = staticmethod(fake_generate_429)

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kw: FakeClient(),
    )
    # 加速测试:把 sleep 时间设 0
    monkeypatch.setattr("time.sleep", lambda _s: None)

    import pytest
    with pytest.raises(LlmCallFailed) as exc_info:
        adapter.generate("test prompt", aspect_ratio="1:1")
    # 第 1 次 + 2 次 retry = 3 次
    assert call_count["n"] == 3
    assert "重试" in str(exc_info.value) or "429" in str(exc_info.value)

    # Case B: fatal 错(401)不应重试
    call_count_b = {"n": 0}

    class FakeAuthError(Exception):
        def __init__(self):
            super().__init__("Error code: 401 - AuthenticationError")

    def fake_generate_401(*args, **kwargs):
        call_count_b["n"] += 1
        raise FakeAuthError()

    class FakeClientB:
        class images:
            generate = staticmethod(fake_generate_401)

    monkeypatch.setattr("openai.OpenAI", lambda **kw: FakeClientB())

    with pytest.raises(LlmCallFailed) as exc_info_b:
        adapter.generate("test prompt", aspect_ratio="1:1")
    # 401 fatal,只调一次,不 retry
    assert call_count_b["n"] == 1


def test_upload_references_schema_accepts_empty_array():
    """Sprint 5.4.1:UploadReferenceImagesRequest 接受 0 张(跳过模式)。"""
    from app.schemas.comic import UploadReferenceImagesRequest

    # 0 张 OK(跳过)
    req = UploadReferenceImagesRequest(image_urls=[])
    assert req.image_urls == []
    # 3 张 OK(有图)
    req3 = UploadReferenceImagesRequest(
        image_urls=["http://a", "http://b", "http://c"]
    )
    assert len(req3.image_urls) == 3


def test_upload_references_schema_rejects_partial():
    """Sprint 5.4.1:1 / 2 / 4 张应被 422(半残状态防御)。"""
    import pytest
    from pydantic import ValidationError
    from app.schemas.comic import UploadReferenceImagesRequest

    for invalid_len in (1, 2):
        with pytest.raises(ValidationError) as exc_info:
            UploadReferenceImagesRequest(image_urls=["http://a"] * invalid_len)
        assert "0 张" in str(exc_info.value) or "3 张" in str(exc_info.value)

    # 4 张被 max_length=3 卡(更早一道 validator)
    with pytest.raises(ValidationError):
        UploadReferenceImagesRequest(image_urls=["http://a"] * 4)


def test_upload_references_endpoint_accepts_skip_mode(client: TestClient, make_user):
    """Sprint 5.4.1:POST /api/comics/{id}/upload_references 接受 image_urls=[](跳过模式)。

    端到端:LLM 会调用真实 vendor → 此处 mock 主路径让 schema + state 流程能跑通。
    """
    from app.db import get_connection
    from app.services.comic_service import _new_comic_id, _now_iso

    u = make_user("skip_upload_e2e")
    _upgrade_to_max(u["user_id"])

    # 造一个 state='style_uploading' 的 comic(只验 schema + 状态机入口,不真跑 LLM)
    conn = get_connection()
    try:
        comic_id = _new_comic_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO comic_projects
               (id, user_id, name, source_json, state, progress_percent,
                generation_seed, target_pages, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'style_uploading', 10, 111, 6, ?, ?)""",
            (
                comic_id, u["user_id"], "跳过模式 e2e",
                '{"type": "internal", "simulation_ids": []}',
                now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # 调端点(LLM 调用会失败,但 schema/router 接受 [] 是关键测试点)
    r = client.post(
        f"/api/comics/{comic_id}/upload_references",
        headers=u["headers"],
        json={"image_urls": []},
    )
    # 关键断言:不应被 Pydantic schema validation 卡(原 min_length=3 会卡这里)
    # downstream agent 错(scripter/source 没数据等)是允许的 — 那证明 schema 已通过
    body = r.json()
    if r.status_code == 422:
        # 必须是业务级 422(detail 是 dict 含 code),不是 Pydantic schema 错
        # Pydantic schema 错的 detail 是 list[{loc, msg, type}]
        detail = body.get("detail")
        assert isinstance(detail, dict), (
            f"422 应是业务错(detail dict),不是 schema validation 错"
            f"(detail list 表示 image_urls 没通过校验);response={body}"
        )
        # 业务 422 应有 code 字段(如 AGENT_FAILED / SOURCE_TOO_SHORT),不是 image_urls 字段错
        assert "code" in detail, f"业务 422 应有 code:{detail}"


def test_style_director_v3_qwen_prompt_has_content_decoupling_rule():
    """v3 代码里硬编的 Qwen-VL prompt 含内容解耦铁律(防 content leakage)。"""
    # 读 comic_service.py 源码,搜 qwen_dna_prompt 块
    from pathlib import Path
    src = (
        Path(__file__).parent.parent / "app" / "services" / "comic_service.py"
    ).read_text(encoding="utf-8")
    # 找 Stage 1 Qwen-VL prompt 块
    assert "内容-风格解耦铁律" in src or "Content-Agnostic" in src
    # 12 字段全在硬编 prompt 里
    for field in [
        "brush_style", "coloring", "line_work", "character_proportion",
        "lighting_logic", "composition", "color_palette",
        "mood", "atmosphere", "shadow_tone", "inspirations",
        "overall_style_tag",
    ]:
        assert f'"{field}"' in src, f"Qwen-VL v3 prompt 缺字段 {field}"


# ============================================================
# Sprint 5.10+(2026-05-14):retry_style_candidates 端点测试
# ============================================================

def _seed_style_voting_comic(
    client: TestClient, u: dict, with_prompt: bool = True,
    with_candidates: bool = True,
) -> str:
    """造一个 state='style_voting' 的 comic,便于 retry 端点 happy-path 测试。

    Args:
        with_prompt: 是否塞 style_detailed_prompt(False 时模拟 409 NO_STYLE_PROMPT)
        with_candidates: 是否塞 3 个 candidates(含 variant_hint)
    """
    from app.db import get_connection

    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "retry-test",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    comic_id = r.json()["id"]

    conn = get_connection()
    try:
        # 模拟 Stage 1+2 已完成、Stage 3 失败 → style_voting 状态等用户处理
        candidates_json = json.dumps([
            {"index": 1, "variant_hint": "重氛围光", "image_url": None,
             "error": "ImageGenError: SF 503 limit"},
            {"index": 2, "variant_hint": "中性平衡", "image_url": None,
             "error": "ImageGenError: SF 503 limit"},
            {"index": 3, "variant_hint": "强光影对比", "image_url": None,
             "error": "ImageGenError: SF 503 limit"},
        ], ensure_ascii=False) if with_candidates else None
        conn.execute(
            "UPDATE comic_projects SET state=?, progress_percent=?, "
            "style_detailed_prompt=?, style_candidates_json=? WHERE id=?",
            (
                "style_voting", 68,
                "anime style, vibrant colors, detailed line art"
                if with_prompt else None,
                candidates_json,
                comic_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return comic_id


def test_retry_style_candidates_404_when_comic_not_found(
    client: TestClient, make_user,
):
    """非自己的 comic / 不存在 → 404 COMIC_NOT_FOUND。"""
    u = make_user("retry_404")
    r = client.post(
        f"/api/comics/{uuid.uuid4().hex}/retry_style_candidates",
        headers=u["headers"],
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "COMIC_NOT_FOUND"


def test_retry_style_candidates_409_when_state_not_style_voting(
    client: TestClient, make_user,
):
    """state='queued' 时 retry → 409 INVALID_STATE_FOR_RETRY。"""
    u = make_user("retry_wrong_state")
    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "wrong-state",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    comic_id = r.json()["id"]   # state=queued

    r2 = client.post(
        f"/api/comics/{comic_id}/retry_style_candidates",
        headers=u["headers"],
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "INVALID_STATE_FOR_RETRY"


def test_retry_style_candidates_409_when_no_style_prompt(
    client: TestClient, make_user,
):
    """state='style_voting' 但 style_detailed_prompt 为空 → 409 NO_STYLE_PROMPT。"""
    u = make_user("retry_no_prompt")
    comic_id = _seed_style_voting_comic(client, u, with_prompt=False)

    r = client.post(
        f"/api/comics/{comic_id}/retry_style_candidates",
        headers=u["headers"],
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NO_STYLE_PROMPT"


def test_retry_style_candidates_success_replaces_candidates(
    client: TestClient, make_user, monkeypatch,
):
    """成功路径:image_gen 全成功 → 3 个新 candidate(image_url 非空)落 db,
    state 仍 style_voting,等用户重新投票。"""
    from app.services.llm_routing import ImageResult
    from app.services.llm_routing import router as llm_router

    u = make_user("retry_ok")
    comic_id = _seed_style_voting_comic(client, u)

    counter = {"n": 0}

    def fake_generate(self, prompt: str, **kwargs):
        counter["n"] += 1
        return ImageResult(
            url=f"https://fake.cdn/retry/{counter['n']}.png",
            image_count=1,
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    class FakeImageGen:
        def generate(self, prompt, **kwargs):
            return fake_generate(self, prompt, **kwargs)

    # 注入 fake image_gen,绕过 vendor 真调用
    monkeypatch.setattr(llm_router, "_image_gen_cache", FakeImageGen())

    r = client.post(
        f"/api/comics/{comic_id}/retry_style_candidates",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "style_voting"   # 仍待投票
    cands = body["style_candidates"]
    assert len(cands) == 3
    assert all(c.get("image_url", "").startswith("https://fake.cdn/retry/") for c in cands)
    assert counter["n"] == 3   # 准确 3 次 image_gen 调用


def _replace_settings_in_jimeng(monkeypatch, **overrides):
    """helper:Settings 是 frozen dataclass,用 dataclasses.replace 造新实例后
    替换 jimeng_image 模块对 settings 的引用(类比 test_extract.py 的 patch 法)。"""
    import dataclasses
    from app.services.llm_routing.adapters import jimeng_image as _ji
    new_settings = dataclasses.replace(_ji.settings, **overrides)
    monkeypatch.setattr(_ji, "settings", new_settings)


# ============================================================
# Sprint 5.11 Reflexion(2026-05-14):visual verifier + speaker-missing 重生测试
# ============================================================

def _seed_comic_with_character_card(
    client: TestClient, u: dict, char_name: str, descriptor: str,
) -> tuple[str, str]:
    """造一个 comic + 1 张 character_card,返回 (comic_id, character_id)。

    给 _agent_visual_verifier / _maybe_regenerate_panel_for_speaker_missing 测试用。
    """
    from app.db import get_connection

    _upgrade_to_max(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "reflexion-test",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    comic_id = r.json()["id"]

    char_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        from app.services.comic_service import _now_iso
        now = _now_iso()
        conn.execute(
            """INSERT INTO character_cards (id, comic_id, character_id, character_name,
                                              descriptor, card_image_url,
                                              regenerated_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (char_id, comic_id, char_id, char_name, descriptor,
             "https://fake.cdn/card.png", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    return comic_id, char_id


def test_visual_verifier_skip_when_no_dialogue(client: TestClient, make_user):
    """Sprint 5.11:panel 无 dialogues(纯过场)→ verifier 直接 skip,不调 vendor。"""
    from app.db import get_connection
    from app.services.comic_service import _agent_visual_verifier, get_comic_or_404

    u = make_user("verifier_skip_no_dialogue")
    comic_id, _ = _seed_comic_with_character_card(
        client, u, "张凡", "18 岁高中男生,黑色短发,灰色卫衣...",
    )

    conn = get_connection()
    try:
        comic = get_comic_or_404(conn, comic_id, u["user_id"])
        result = _agent_visual_verifier(
            comic,
            {"panel_index": 1, "dialogues": [], "characters": ["张凡"]},
            "https://fake.cdn/img.png",
            conn,
        )
    finally:
        conn.close()

    assert result["verdict"] == "skip"
    assert result["skip_reason"] == "no_dialogue"
    assert result["usage"]["input_tokens"] == 0   # 未调 vendor


def test_visual_verifier_pass_when_speaker_visible(
    client: TestClient, make_user, monkeypatch,
):
    """Sprint 5.11:Qwen-VL 报 speaker_visible 含全部 expected → verdict=pass。"""
    from app.db import get_connection
    from app.services.comic_service import _agent_visual_verifier, get_comic_or_404
    from app.services.llm_routing import router as llm_router

    u = make_user("verifier_pass")
    comic_id, _ = _seed_comic_with_character_card(
        client, u, "张凡", "18 岁高中男生,黑色短发,灰色连帽卫衣,瘦削身材...",
    )

    class FakeVision:
        def describe(self, image_url, question, *, max_tokens=1000, timeout=30.0):
            payload = {
                "person_count": 1,
                "speakers_visible": ["张凡"],
                "verdict_brief": "张凡明显在画面里(灰卫衣+黑短发对得上)",
            }
            return json.dumps(payload, ensure_ascii=False), {
                "input_tokens": 500, "output_tokens": 80,
            }

    monkeypatch.setattr(llm_router, "_vision_llm_cache", FakeVision())

    conn = get_connection()
    try:
        comic = get_comic_or_404(conn, comic_id, u["user_id"])
        result = _agent_visual_verifier(
            comic,
            {
                "panel_index": 1,
                "dialogues": [{"speaker": "张凡", "text": "真的发了五百块!"}],
                "characters": ["张凡"],
            },
            "https://fake.cdn/img.png",
            conn,
        )
    finally:
        conn.close()

    assert result["verdict"] == "pass"
    assert result["fail_reasons"] == []
    assert result["speakers_visible"] == ["张凡"]
    assert result["speakers_missing"] == []
    assert result["person_count"] == 1
    assert result["usage"]["input_tokens"] == 500   # 真调了 vendor


def test_visual_verifier_fail_when_speaker_missing(
    client: TestClient, make_user, monkeypatch,
):
    """Sprint 5.11:expected speaker 不在 speakers_visible → verdict=fail + reasons=[speaker_missing]。"""
    from app.db import get_connection
    from app.services.comic_service import _agent_visual_verifier, get_comic_or_404
    from app.services.llm_routing import router as llm_router

    u = make_user("verifier_fail")
    comic_id, _ = _seed_comic_with_character_card(
        client, u, "地府守门人", "中年男性,黑长袍,苍白皮肤,左脸竖疤...",
    )
    # 同时种一个 张凡 让 verifier 有完整 expected 列表
    from app.db import get_connection as _gc
    conn = _gc()
    try:
        from app.services.comic_service import _now_iso
        conn.execute(
            """INSERT INTO character_cards (id, comic_id, character_id, character_name,
                                              descriptor, card_image_url,
                                              regenerated_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (uuid.uuid4().hex, comic_id, uuid.uuid4().hex, "张凡",
             "18 岁高中男生,灰卫衣...", "https://fake.cdn/card2.png",
             _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    class FakeVision:
        def describe(self, image_url, question, *, max_tokens=1000, timeout=30.0):
            # Qwen-VL 看到画面里只有张凡,地府守门人未出现
            payload = {
                "person_count": 1,
                "speakers_visible": ["张凡"],   # 地府守门人 missing
                "verdict_brief": "只看到张凡(灰卫衣),地府守门人(黑袍+疤痕)未出现",
            }
            return json.dumps(payload, ensure_ascii=False), {
                "input_tokens": 520, "output_tokens": 95,
            }

    monkeypatch.setattr(llm_router, "_vision_llm_cache", FakeVision())

    conn = get_connection()
    try:
        comic = get_comic_or_404(conn, comic_id, u["user_id"])
        result = _agent_visual_verifier(
            comic,
            {
                "panel_index": 2,
                "dialogues": [
                    {"speaker": "地府守门人", "text": "张凡完成任务,500 元红包已发"},
                    {"speaker": "张凡", "text": "(惊讶看着手机)"},
                ],
                "characters": ["地府守门人", "张凡"],
            },
            "https://fake.cdn/img.png",
            conn,
        )
    finally:
        conn.close()

    assert result["verdict"] == "fail"
    assert "speaker_missing" in result["fail_reasons"]
    assert "地府守门人" in result["speakers_missing"]
    assert "张凡" not in result["speakers_missing"]   # 张凡出现了


def test_maybe_regenerate_no_op_when_verifier_passed(
    client: TestClient, make_user,
):
    """Sprint 5.11:verifier pass → maybe_regenerate 直接返 None (不浪费 vendor 调用)。"""
    from app.db import get_connection
    from app.services.comic_service import (
        _maybe_regenerate_panel_for_speaker_missing, get_comic_or_404,
    )

    u = make_user("regen_noop_pass")
    comic_id, _ = _seed_comic_with_character_card(
        client, u, "张凡", "18 岁高中男生...",
    )

    conn = get_connection()
    try:
        comic = get_comic_or_404(conn, comic_id, u["user_id"])
        new_url, usage, new_prompt = _maybe_regenerate_panel_for_speaker_missing(
            comic, {"panel_index": 1},
            "原始 director prompt 内容",
            {"verdict": "pass", "fail_reasons": [], "speakers_missing": []},
            conn,
        )
    finally:
        conn.close()

    assert new_url is None
    assert new_prompt is None
    assert usage == {}


def test_maybe_regenerate_triggers_on_speaker_missing(
    client: TestClient, make_user, monkeypatch,
):
    """Sprint 5.11:verdict=fail + speaker_missing + descriptor 可拉 → 触发重生,
    新 prompt 头部含 [CRITICAL: ...] 注入块,image_gen 真被再调一次。"""
    from app.db import get_connection
    from app.services.comic_service import (
        _maybe_regenerate_panel_for_speaker_missing, get_comic_or_404,
    )
    from app.services.llm_routing import ImageResult
    from app.services.llm_routing import router as llm_router

    u = make_user("regen_trigger")
    comic_id, _ = _seed_comic_with_character_card(
        client, u, "地府守门人", "中年男性,黑长袍,苍白皮肤,左脸竖疤,眼神冷峻",
    )

    captured_prompt = {"val": None}

    class FakeImageGen:
        def generate(self, prompt, **kwargs):
            captured_prompt["val"] = prompt
            return ImageResult(
                url="https://fake.cdn/regen.png",
                image_count=1,
                usage={"input_tokens": 0, "output_tokens": 0},
            )

    monkeypatch.setattr(llm_router, "_image_gen_cache", FakeImageGen())

    conn = get_connection()
    try:
        comic = get_comic_or_404(conn, comic_id, u["user_id"])
        new_url, usage, new_prompt = _maybe_regenerate_panel_for_speaker_missing(
            comic, {"panel_index": 2},
            "原始 director prompt 内容",
            {
                "verdict": "fail",
                "fail_reasons": ["speaker_missing"],
                "speakers_missing": ["地府守门人"],
            },
            conn,
        )
    finally:
        conn.close()

    assert new_url == "https://fake.cdn/regen.png"
    assert new_prompt is not None
    assert new_prompt.startswith("[CRITICAL:")   # 头部硬注入
    assert "地府守门人" in new_prompt
    assert "黑长袍" in new_prompt   # descriptor 完整带入
    assert "原始 director prompt 内容" in new_prompt   # 原 prompt 保留
    # image_gen 真被调
    assert captured_prompt["val"] is not None
    assert "[CRITICAL:" in captured_prompt["val"]


def test_build_speaker_grounding_injection_no_descriptors_returns_none():
    """Sprint 5.11 helper:speakers_missing 非空但 speakers_with_desc 空 → 返回 None
    (拿不到 descriptor 时不应硬塞空块,降级 = 不重生)。"""
    from app.services.comic_service import _build_speaker_grounding_injection

    assert _build_speaker_grounding_injection(["地府守门人"], []) is None
    assert _build_speaker_grounding_injection([], [{"name": "张凡", "descriptor": "..."}]) is None
    # 有 missing 但 desc 都空字符串 → 也返回 None
    assert _build_speaker_grounding_injection(
        ["地府守门人"], [{"name": "地府守门人", "descriptor": ""}],
    ) is None


def test_image_adapter_detect_vendor_zhipu(monkeypatch):
    """Sprint 5.10++(2026-05-14):adapter 按 api_base 自动探测智谱 BigModel,
    映射到 zhipu 分支(走 OpenAI 标准 size,不接 n 字段),不需新依赖。"""
    from app.services.llm_routing.adapters.jimeng_image import (
        _detect_vendor, _vendor_label, _ZHIPU_ASPECT_TO_SIZE,
    )

    # zhipu 域名(标准)
    _replace_settings_in_jimeng(
        monkeypatch,
        jimeng_api_base="https://open.bigmodel.cn/api/paas/v4",
        jimeng_model="cogview-4-250304",
    )
    assert _detect_vendor() == "zhipu"
    assert "Zhipu-CogView" in _vendor_label()
    assert "cogview-4-250304" in _vendor_label()

    # zhipu 域名变体(open.bigmodel 子串识别)
    _replace_settings_in_jimeng(
        monkeypatch,
        jimeng_api_base="https://test.open.bigmodel.com/v4",
    )
    assert _detect_vendor() == "zhipu"

    # ARK / SiliconFlow 不被误判为 zhipu
    _replace_settings_in_jimeng(
        monkeypatch,
        jimeng_api_base="https://ark.cn-beijing.volces.com/api/v3",
    )
    assert _detect_vendor() == "ark"
    _replace_settings_in_jimeng(
        monkeypatch,
        jimeng_api_base="https://api.siliconflow.cn/v1",
    )
    assert _detect_vendor() == "siliconflow"

    # zhipu size 映射:5 个 aspect_ratio 全覆盖,值在 cogview-4 支持的尺寸里
    assert _ZHIPU_ASPECT_TO_SIZE["1:1"] == "1024x1024"
    assert _ZHIPU_ASPECT_TO_SIZE["4:3"] == "1152x864"
    assert _ZHIPU_ASPECT_TO_SIZE["3:4"] == "864x1152"
    assert _ZHIPU_ASPECT_TO_SIZE["16:9"] == "1440x720"
    assert _ZHIPU_ASPECT_TO_SIZE["9:16"] == "720x1440"


def test_image_adapter_zhipu_call_body_excludes_n_field(monkeypatch):
    """Sprint 5.10++:zhipu 分支必须**不传 n 字段**(CogView 不接受 n,只单图),
    且 size 字段从 _ZHIPU_ASPECT_TO_SIZE 取值。"""
    from app.services.llm_routing.adapters.jimeng_image import JimengImageAdapter

    _replace_settings_in_jimeng(
        monkeypatch,
        jimeng_api_base="https://open.bigmodel.cn/api/paas/v4",
        jimeng_api_key="test-key-12345",
        jimeng_model="cogview-4-250304",
    )

    captured: dict = {}

    def fake_generate(*args, **kwargs):
        captured.update(kwargs)
        # 模拟 OpenAI 兼容返回
        class FakeData:
            url = "https://fake.zhipu.cdn/test.png"
        class FakeResp:
            data = [FakeData()]
        return FakeResp()

    class FakeClient:
        class images:
            generate = staticmethod(fake_generate)

    monkeypatch.setattr("openai.OpenAI", lambda **kw: FakeClient())

    adapter = JimengImageAdapter()
    result = adapter.generate("test prompt", aspect_ratio="3:4")

    # 关键断言:
    assert "n" not in captured, f"zhipu 不应传 n 字段:{captured}"
    assert "extra_body" not in captured, f"zhipu 不应走 SF extra_body 路径:{captured}"
    assert captured.get("size") == "864x1152", "3:4 应取 864x1152"
    assert captured.get("model") == "cogview-4-250304"
    assert "test prompt" in captured.get("prompt", "")
    assert result.url == "https://fake.zhipu.cdn/test.png"


def test_retry_style_candidates_503_when_all_fail(
    client: TestClient, make_user, monkeypatch,
):
    """vendor 全失败 → 503 RETRY_STILL_FAILED + errors 详情。"""
    from app.services.llm_routing import router as llm_router

    u = make_user("retry_all_fail")
    comic_id = _seed_style_voting_comic(client, u)

    class FailingImageGen:
        def generate(self, prompt, **kwargs):
            raise RuntimeError("SiliconFlow 503 rate limit")

    monkeypatch.setattr(llm_router, "_image_gen_cache", FailingImageGen())

    r = client.post(
        f"/api/comics/{comic_id}/retry_style_candidates",
        headers=u["headers"],
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "RETRY_STILL_FAILED"
    assert "errors" in detail and len(detail["errors"]) > 0
    # 错误信息含 vendor 实际异常名(便于用户排错)
    assert any("RuntimeError" in e for e in detail["errors"])


# ============================================================
# Sprint 5.B(2026-05-18)漫画态降级:comics_per_month 次数池闸门测试
# ============================================================

def test_create_comic_free_plan_blocked_by_plan_gate(
    client: TestClient, make_user,
):
    """2026-06-02 产品决策:Free 用户创建漫画 → 403 COMIC_PLAN_REQUIRED.

    历史:Free 用 comics_per_month=0 配额阻塞(429).现改为更明确的 plan 校验:
    "漫创态是会员专属功能,升级 Pro 即可解锁"(403).
    """
    u = make_user("comic_free_plan_gate")
    # 不升档,保持 free
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "free 试创",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "COMIC_PLAN_REQUIRED"
    assert "会员" in detail["message"] or "Pro" in detail["message"]


def test_create_comic_pro_plan_one_quota_then_blocked(
    client: TestClient, make_user, monkeypatch,
):
    """Pro 用户:ECON-1 后 comics_per_month=0,任何 plan 创建都 429.
    历史行为(comics=1 → 第 2 本 429)用 monkeypatch 临时还原验证.
    ECON-2 漫画包 sprint 完成后,本测试改回测"购买漫画包后能创建"."""
    from app.services.quota_service import PLAN_LIMITS, PlanLimits

    # 临时还原旧 Pro=1 配额(测试快照机制 + quota enforce 逻辑)
    old_pro = PLAN_LIMITS["pro"]
    monkeypatch.setitem(PLAN_LIMITS, "pro", PlanLimits(
        monthly_credits_quota=old_pro.monthly_credits_quota,
        single_credit_price_cents=old_pro.single_credit_price_cents,
        characters_per_project=old_pro.characters_per_project,
        projects_total=old_pro.projects_total,
        reshape_max_percent=old_pro.reshape_max_percent,
        comics_per_month=1,
    ))

    u = make_user("comic_pro_quota")
    _upgrade_to_plan(u["user_id"], "pro")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    # 第 1 本应成功
    r1 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "pro 第 1 本",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r1.status_code == 201

    # 第 2 本应 429
    r2 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "pro 第 2 本",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r2.status_code == 429
    detail = r2.json()["detail"]
    assert detail["code"] == "QUOTA_EXCEEDED"
    assert detail["kind"] == "comics_per_month"
    assert detail["used"] == 1
    assert detail["limit"] == 1


def test_count_user_comics_excludes_cancelled(
    client: TestClient, make_user,
):
    """Sprint 5.B 规则:cancelled 的漫画不占用配额(用户重新可创建)。"""
    from app.db import get_connection
    from app.services.quota_service import _count_user_comics_this_month

    u = make_user("comic_cancelled_excl")
    _upgrade_to_plan(u["user_id"], "pro")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    # 创建一本
    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "pro 本",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 201
    comic_id = r.json()["id"]

    conn = get_connection()
    try:
        # 初始 count = 1
        assert _count_user_comics_this_month(conn, u["user_id"]) == 1
        # 改为 cancelled
        conn.execute(
            "UPDATE comic_projects SET state='cancelled' WHERE id=?", (comic_id,),
        )
        conn.commit()
        # 应不计入
        assert _count_user_comics_this_month(conn, u["user_id"]) == 0
    finally:
        conn.close()

    # 再创一本应成功(配额释放)
    r2 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "重创",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r2.status_code == 201


def test_count_user_comics_excludes_failed(client: TestClient, make_user):
    """Sprint 5.B 规则:failed 的漫画不占用配额(失败不二次惩罚用户)。"""
    from app.db import get_connection
    from app.services.quota_service import _count_user_comics_this_month

    u = make_user("comic_failed_excl")
    _upgrade_to_plan(u["user_id"], "pro")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "失败本",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 201
    comic_id = r.json()["id"]

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_projects SET state='failed' WHERE id=?", (comic_id,),
        )
        conn.commit()
        assert _count_user_comics_this_month(conn, u["user_id"]) == 0
    finally:
        conn.close()


def test_create_comic_super_max_four_per_month(client: TestClient, make_user, monkeypatch):
    """超级 Max:ECON-1 后 comics_per_month=0,测试用 monkeypatch 还原旧 4 本配额逻辑."""
    from app.services.quota_service import PLAN_LIMITS, PlanLimits

    old_sm = PLAN_LIMITS["super_max"]
    monkeypatch.setitem(PLAN_LIMITS, "super_max", PlanLimits(
        monthly_credits_quota=old_sm.monthly_credits_quota,
        single_credit_price_cents=old_sm.single_credit_price_cents,
        characters_per_project=old_sm.characters_per_project,
        projects_total=old_sm.projects_total,
        reshape_max_percent=old_sm.reshape_max_percent,
        comics_per_month=4,
    ))

    u = make_user("comic_super_max_quota")
    _upgrade_to_plan(u["user_id"], "super_max")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    for i in range(4):
        r = client.post(
            "/api/comics", headers=u["headers"],
            json={"name": f"super 第 {i + 1} 本",
                  "source": {"type": "internal", "simulation_ids": [sim_id]}},
        )
        assert r.status_code == 201, f"第 {i + 1} 本应成功 (super_max quota=4):{r.json()}"

    # 第 5 本超额
    r5 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "super 第 5 本",
              "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r5.status_code == 429
    assert r5.json()["detail"]["kind"] == "comics_per_month"
    assert r5.json()["detail"]["limit"] == 4


def test_plan_limits_comics_per_month_values():
    """ECON-1(2026-05-27 末⁴):漫创态全档清零,改为单买漫画包(ECON-2 sprint)."""
    from app.services.quota_service import PLAN_LIMITS

    assert PLAN_LIMITS["free"].comics_per_month == 0
    assert PLAN_LIMITS["pro"].comics_per_month == 0          # ECON-1: 1 → 0
    assert PLAN_LIMITS["max"].comics_per_month == 0          # ECON-1: 2 → 0
    assert PLAN_LIMITS["super_max"].comics_per_month == 0    # ECON-1: 4 → 0
    assert PLAN_LIMITS["founder"].comics_per_month >= 999999
