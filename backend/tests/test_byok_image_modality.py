"""
BYOK 图像模态(modality)+ 漫创态解锁准入 —— v5(2026-07-02)item2/item4/item6 测试。

覆盖:
  1. modality 隔离:同一用户可各留一个 text 默认 + 一个 image 默认,互不覆盖
  2. get_active_llm_config 只取 text;get_active_image_config 只取 image
  3. get_active_image_config 依赖有效 BYOK 订阅(无订阅返 None)
  4. 漫创态准入闸门:
     - free 无 BYOK          → 403 COMIC_ACCESS_REQUIRED
     - free + BYOK 仅文本     → 403 COMIC_IMAGE_KEY_REQUIRED
     - free + BYOK 文本+图像  → 201(解锁,创建成功)
     - 订阅 pro              → 201(订阅解锁,无需 BYOK 图像)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.byok import BYOKConfigUpsertRequest
from app.services import byok_service


# ============================================================
# Helpers
# ============================================================

def _activate_byok(conn, user_id: str) -> None:
    """给用户开通一张有效 BYOK 月卡(购买 + 激活)。"""
    sub = byok_service.purchase_subscription(conn, user_id, months=1)
    byok_service.activate_code(conn, user_id, sub.code)


def _add_text_config(conn, user_id: str, *, is_default: bool = True) -> None:
    byok_service.upsert_config(
        conn, user_id,
        BYOKConfigUpsertRequest(
            provider="deepseek",
            base_url="https://api.deepseek.com/v1",
            model_name="deepseek-chat",
            api_key="sk-TEXT-KEY-abcd",
            is_default=is_default,
            modality="text",
        ),
    )


def _add_image_config(conn, user_id: str, *, is_default: bool = True) -> None:
    byok_service.upsert_config(
        conn, user_id,
        BYOKConfigUpsertRequest(
            provider="siliconflow",
            base_url="https://api.siliconflow.cn/v1",
            model_name="Kwai-Kolors/Kolors",
            api_key="sk-IMAGE-KEY-wxyz",
            is_default=is_default,
            modality="image",
        ),
    )


def _upgrade_plan(user_id: str, plan: str) -> None:
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, user_id))
        conn.commit()
    finally:
        conn.close()


def _seed_done_simulation(client: TestClient, headers: dict, user_id: str) -> str:
    """造一个 state='done' 的 simulation 作 comic internal source(直插 db)。
    schema 对齐 test_comic_service.py 的同名 helper。"""
    import json
    from app.db import get_connection
    from app.services.comic_service import _new_comic_id, _now_iso

    conn = get_connection()
    try:
        sim_id = _new_comic_id()
        proj_id = _new_comic_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, custom_type_name,
                                       tags, mode, created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, ?, 'novel', NULL, '[]', 'initial', ?, ?, '{}', 30)""",
            (proj_id, user_id, "测试项目", now, now),
        )
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
                json.dumps([{"id": "char1_id", "name": "测试主角"}], ensure_ascii=False),
                "测试 narrative 内容用于漫画态;" * 50,
                now, now, now,
            ),
        )
        conn.commit()
        return sim_id
    finally:
        conn.close()


# ============================================================
# 1. modality 隔离
# ============================================================

def test_text_and_image_defaults_coexist(make_user):
    """一个用户同时配 text 默认 + image 默认,两个 is_default 都保留(不互相清)。"""
    from app.db import get_connection
    u = make_user("modality_coexist")
    conn = get_connection()
    try:
        _add_text_config(conn, u["user_id"], is_default=True)
        _add_image_config(conn, u["user_id"], is_default=True)

        configs = byok_service.list_configs(conn, u["user_id"])
        text_defaults = [c for c in configs if c.modality == "text" and c.is_default]
        image_defaults = [c for c in configs if c.modality == "image" and c.is_default]
        assert len(text_defaults) == 1, "文本默认应保留 1 个"
        assert len(image_defaults) == 1, "图像默认应保留 1 个(不被文本清掉)"
    finally:
        conn.close()


def test_get_active_configs_pick_correct_modality(make_user):
    """get_active_llm_config 只取 text;get_active_image_config 只取 image。"""
    from app.db import get_connection
    u = make_user("modality_pick")
    conn = get_connection()
    try:
        _activate_byok(conn, u["user_id"])
        _add_text_config(conn, u["user_id"])
        _add_image_config(conn, u["user_id"])

        text_cfg = byok_service.get_active_llm_config(conn, u["user_id"])
        image_cfg = byok_service.get_active_image_config(conn, u["user_id"])
        assert text_cfg is not None and text_cfg["model_name"] == "deepseek-chat"
        assert text_cfg["api_key"] == "sk-TEXT-KEY-abcd"
        assert image_cfg is not None and image_cfg["model_name"] == "Kwai-Kolors/Kolors"
        assert image_cfg["api_key"] == "sk-IMAGE-KEY-wxyz"
    finally:
        conn.close()


def test_image_config_requires_active_subscription(make_user):
    """无有效 BYOK 订阅时,即便配了图像模型,get_active_image_config 也返 None。"""
    from app.db import get_connection
    u = make_user("image_no_sub")
    conn = get_connection()
    try:
        _add_image_config(conn, u["user_id"])   # 配了但没开订阅
        assert byok_service.get_active_image_config(conn, u["user_id"]) is None
    finally:
        conn.close()


def test_image_config_none_when_only_text(make_user):
    """有订阅 + 只配了文本 → 图像 config 返 None(漫创态该被拦)。"""
    from app.db import get_connection
    u = make_user("only_text")
    conn = get_connection()
    try:
        _activate_byok(conn, u["user_id"])
        _add_text_config(conn, u["user_id"])
        assert byok_service.get_active_image_config(conn, u["user_id"]) is None
        assert byok_service.get_active_llm_config(conn, u["user_id"]) is not None
    finally:
        conn.close()


# ============================================================
# 2. 漫创态准入闸门
# ============================================================

def test_comic_gate_free_no_byok_blocked(client: TestClient, make_user):
    u = make_user("gate_free_nobyok")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])
    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "试", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "COMIC_ACCESS_REQUIRED"


def test_comic_gate_byok_text_only_needs_image(client: TestClient, make_user):
    from app.db import get_connection
    u = make_user("gate_text_only")
    conn = get_connection()
    try:
        _activate_byok(conn, u["user_id"])
        _add_text_config(conn, u["user_id"])
    finally:
        conn.close()
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])
    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "试", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "COMIC_IMAGE_KEY_REQUIRED"


def test_comic_gate_byok_with_image_unlocks(client: TestClient, make_user):
    """free 用户 + BYOK(文本+图像)→ 漫创态解锁,创建成功(201)。"""
    from app.db import get_connection
    u = make_user("gate_byok_image")
    conn = get_connection()
    try:
        _activate_byok(conn, u["user_id"])
        _add_text_config(conn, u["user_id"])
        _add_image_config(conn, u["user_id"])
    finally:
        conn.close()
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])
    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "BYOK 解锁", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "queued"


def test_comic_gate_subscription_unlocks_without_byok(client: TestClient, make_user):
    """订阅 pro(无 BYOK)→ 漫创态解锁(用平台图像 key),创建成功(201)。"""
    u = make_user("gate_pro_sub")
    _upgrade_plan(u["user_id"], "pro")
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])
    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "订阅解锁", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 201, r.text
