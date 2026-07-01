"""test_comic_pack.py — Sprint ECON-2(2026-05-27 末⁴⁴)漫画包购买流程测试

范围:
  - 购买漫画包 endpoint(POST /credit/comic_pack/purchase)
  - 余量查询 endpoint(GET /credit/comic_pack/available)
  - service 层 helper(count / consume)
  - 创建漫画时自动扣减漫画包
  - 没漫画包时创建漫画 429
  - FIFO 消耗(先买的先用)
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# ============================================================
# Helpers
# ============================================================

def _seed_done_simulation(
    client: TestClient,
    user_headers: dict,
    user_id: str,
) -> str:
    """造一个 state='done' 的 simulation,供 comic source 引用(对齐 test_comic_service)."""
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
                json.dumps(
                    [{"id": "char1_id", "name": "测试主角"}], ensure_ascii=False
                ),
                "测试 narrative 内容用于漫画态;" * 50,
                now, now, now,
            ),
        )
        conn.commit()
        return sim_id
    finally:
        conn.close()


def _upgrade_to_pro(user_id: str) -> None:
    """把测试用户升到 pro 档."""
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# A. 购买漫画包
# ============================================================

def test_purchase_comic_pack_creates_lot(client: TestClient, make_user):
    """POST /credit/comic_pack/purchase 创建一个 lot,返回 id + expires_at."""
    u = make_user("pack_buyer")
    r = client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["lot"]["price_cents"] == 3000
    assert body["lot"]["id"]
    assert body["lot"]["expires_at"]
    assert body["available_packs"] == 1
    assert body["validity_days"] == 180


def test_purchase_two_packs_count_to_2(client: TestClient, make_user):
    """连续买 2 个漫画包 → available_packs == 2."""
    u = make_user("two_packs")
    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    r2 = client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    assert r2.status_code == 201
    assert r2.json()["available_packs"] == 2


def test_get_comic_pack_balance(client: TestClient, make_user):
    """GET /credit/comic_pack/available 返回当前余量."""
    u = make_user("balance_check")
    # 初始 0
    r0 = client.get("/api/credit/comic_pack/available", headers=u["headers"])
    assert r0.status_code == 200
    assert r0.json()["available_packs"] == 0
    # 买 1 个
    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    r1 = client.get("/api/credit/comic_pack/available", headers=u["headers"])
    assert r1.json()["available_packs"] == 1


# ============================================================
# B. 创建漫画扣减漫画包
# ============================================================

def test_create_comic_with_pack_consumes_one(client: TestClient, make_user):
    """v5(2026-07-02)漫画包下线:Pro 订阅即可无限创建,创建**不再消耗**遗留漫画包。"""
    u = make_user("comic_with_pack")
    _upgrade_to_pro(u["user_id"])

    # 遗留:买了包也不会被消耗(机制已下线,仅作向后兼容验证)
    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r1 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "创建 1", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r1.status_code == 201, r1.text

    # 包未被消耗 —— 仍为 1(创建不再扣包)
    r_bal = client.get("/api/credit/comic_pack/available", headers=u["headers"])
    assert r_bal.json()["available_packs"] == 1

    # 再创建仍 201(订阅无次数闸门)
    r2 = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "创建 2", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r2.status_code == 201, r2.text


def test_create_comic_without_pack_returns_429(client: TestClient, make_user):
    """v5(2026-07-02):Pro 无包也能创建 —— 订阅即解锁漫创态,不再需要漫画包。"""
    u = make_user("no_pack_pro")
    _upgrade_to_pro(u["user_id"])
    sim_id = _seed_done_simulation(client, u["headers"], u["user_id"])

    r = client.post(
        "/api/comics", headers=u["headers"],
        json={"name": "无包创建", "source": {"type": "internal", "simulation_ids": [sim_id]}},
    )
    assert r.status_code == 201, r.text


# ============================================================
# C. Service 层 helpers(漫画包购买/计数 endpoint 仍在,但创建不再消耗;dormant 保留)
# ============================================================

def test_consume_one_returns_false_when_no_pack(client: TestClient, make_user):
    """helper:user 无 lot 时 consume 返 False(不抛错,不扣减)."""
    from app.db import get_connection
    from app.services.credit_service import consume_one_comic_pack_if_available

    u = make_user("no_lot_user")
    conn = get_connection()
    try:
        result = consume_one_comic_pack_if_available(conn, u["user_id"], "fake_comic_id")
        assert result is False
    finally:
        conn.close()


def test_count_zero_for_new_user(client: TestClient, make_user):
    """新用户 count_available_comic_packs == 0."""
    from app.db import get_connection
    from app.services.credit_service import count_available_comic_packs

    u = make_user("new_count_user")
    conn = get_connection()
    try:
        assert count_available_comic_packs(conn, u["user_id"]) == 0
    finally:
        conn.close()


# ============================================================
# D. v5(2026-07-02)漫画包机制下线 —— 创建不再消耗、失败/取消不再退还。
#    原 ECON-2.1 退还测试(refund_on_cancel / refund_on_fail / no_refund_on_done)
#    随机制一并删除;购买/计数 endpoint 与 cron 过期仍保留(dormant 兼容)。
# ============================================================


# ============================================================
# E. ECON-2.2 — cron 过期处理(漫画包购买 endpoint 仍在,过期清理照常)
# ============================================================

def test_cron_expire_old_comic_packs(client: TestClient, make_user):
    """cron 把 expires_at < now 的未使用 lot 标记 is_expired=1."""
    from app.db import get_connection
    from app.services.credit_cron import expire_old_comic_packs

    u = make_user("cron_expire_user")

    # 买 1 包(正常 6 月有效期)
    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])

    # 手工把 expires_at 改成过去时刻(模拟时间到了)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_pack_lots SET expires_at='2020-01-01T00:00:00+00:00' "
            "WHERE user_id=?",
            (u["user_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    # 跑 cron
    report = expire_old_comic_packs()
    assert report["lots_scanned"] >= 1
    assert report["lots_expired"] >= 1
    assert report["errors"] == []

    # lot 应被标记 is_expired=1
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT is_expired, expired_at FROM comic_pack_lots WHERE user_id=?",
            (u["user_id"],),
        ).fetchone()
        assert row["is_expired"] == 1
        assert row["expired_at"] is not None
    finally:
        conn.close()


def test_cron_does_not_expire_used_packs(client: TestClient, make_user):
    """cron **不**过期已 used 的 lot(保留 audit trail)."""
    from app.db import get_connection
    from app.services.credit_cron import expire_old_comic_packs

    u = make_user("cron_used_safe")
    _upgrade_to_pro(u["user_id"])

    # 买 1 包,然后**直接 SQL 标记 is_used=1**(v5 起创建漫画不再消耗包,
    # 故不能靠创建来标 used;本测试只验证 cron 对 used lot 的保护语义)
    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_pack_lots SET is_used=1, used_at='2026-07-01T00:00:00+00:00' "
            "WHERE user_id=?",
            (u["user_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    # 手工把 expires_at 改成过去
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_pack_lots SET expires_at='2020-01-01T00:00:00+00:00' "
            "WHERE user_id=?",
            (u["user_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    # 跑 cron — 应该不动 used lot
    expire_old_comic_packs()

    # 验证 lot 仍 is_expired=0,is_used=1
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT is_expired, is_used FROM comic_pack_lots WHERE user_id=?",
            (u["user_id"],),
        ).fetchone()
        assert row["is_expired"] == 0, "used 的 lot 不应被 cron 标记过期"
        assert row["is_used"] == 1
    finally:
        conn.close()


def test_cron_idempotent_already_expired(client: TestClient, make_user):
    """cron 幂等 — 已 is_expired=1 的 lot 不被再处理."""
    from app.db import get_connection
    from app.services.credit_cron import expire_old_comic_packs

    u = make_user("cron_idempotent")

    client.post("/api/credit/comic_pack/purchase", headers=u["headers"])

    # 第 1 次:手工过期 + 跑 cron
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE comic_pack_lots SET expires_at='2020-01-01T00:00:00+00:00' "
            "WHERE user_id=?",
            (u["user_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    r1 = expire_old_comic_packs()
    assert r1["lots_expired"] >= 1

    # 第 2 次:再跑 → 应该 lots_expired == 0(已处理过)
    r2 = expire_old_comic_packs()
    assert r2["lots_expired"] == 0, "幂等:已 is_expired=1 的不再处理"
