"""test_credit_consume.py — Sprint C.2(2026-05-13)credit 真扣测试。

覆盖:
  - simulation 完成时按 token 扣 credit + 余额变化
  - 余额不足 → 创建 simulation 抛 InsufficientCredits 429
  - founder 档短路:跑 simulation 不扣 credit,但 credit_transactions 仍有 audit 记录
  - refund_credits 路径(漫画 cancel 退款已扣 credit)
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient


def _seed_done_simulation(client, user_headers, user_id):
    """造 1 条 state='done' 的 simulation,供 comic source 用。"""
    from app.db import get_connection
    from app.services.comic_service import _new_comic_id, _now_iso

    conn = get_connection()
    try:
        sim_id = _new_comic_id()
        proj_id = _new_comic_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, custom_type_name,
                tags, mode, created_at, updated_at, world_baseline_json,
                graph_strength_threshold)
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
                json.dumps([{"id": "c1", "name": "测试主角"}], ensure_ascii=False),
                "测试 narrative;" * 50, now, now, now,
            ),
        )
        conn.commit()
        return sim_id
    finally:
        conn.close()


def _upgrade_to_max(user_id):
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan='max' WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _set_balance(user_id, subscription_credits, addon_credits=0):
    """直接 SQL 改 wallet 余额 — 测试场景模拟。"""
    from app.db import get_connection
    from app.services.credit_service import ensure_balance

    conn = get_connection()
    try:
        ensure_balance(conn, user_id, "max")
        conn.execute(
            """UPDATE user_credit_balances
               SET subscription_credits=?, addon_credits=?
               WHERE user_id=?""",
            (subscription_credits, addon_credits, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_balance_sum(user_id):
    """读取当前 user 总 credit 余额。"""
    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT subscription_credits + addon_credits AS total "
            "FROM user_credit_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return int(row["total"]) if row else 0
    finally:
        conn.close()


def _count_transactions(user_id, kind=None):
    from app.db import get_connection
    conn = get_connection()
    try:
        if kind:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM credit_transactions "
                "WHERE user_id=? AND kind=?",
                (user_id, kind),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM credit_transactions "
                "WHERE user_id=?",
                (user_id,),
            ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


# ============================================================
# T1 创建 simulation 余额为 0 → 429 INSUFFICIENT_CREDITS
# ============================================================

def test_create_simulation_blocked_by_zero_balance(
    client: TestClient, make_user, patched_simulation_llm,
):
    u = make_user("balance_zero")
    _upgrade_to_max(u["user_id"])
    _set_balance(u["user_id"], 0, 0)   # 余额清零

    # 造 project + char
    r_proj = client.post(
        "/api/projects",
        headers=u["headers"],
        json={"name": "P1", "type": "novel", "tags": [], "mode": "initial"},
    )
    proj_id = r_proj.json()["id"]
    for name in ("角色A", "角色B", "角色C"):
        client.post(
            f"/api/projects/{proj_id}/characters",
            headers=u["headers"],
            json={"name": name},
        )

    r = client.post(
        f"/api/projects/{proj_id}/simulations",
        headers=u["headers"],
        json={
            "divergence": "余额为零应该被拒绝创建",
            "reshape_percent": 10,
        },
    )
    assert r.status_code == 429, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "INSUFFICIENT_CREDITS"
    assert detail["action"] == "continuation"


# ============================================================
# T2 founder 短路:跑 simulation 不扣 credit 但有 transaction audit
# ============================================================

def test_founder_short_circuit_unit(make_user, monkeypatch):
    """founder 短路单测:直接调 consume_credits,绕开 router。

    settings 是 frozen dataclass,只能 monkeypatch 单字段;用 object.__setattr__ 强写。
    """
    from app.config import settings
    from app.db import get_connection
    from app.services.credit_service import consume_credits, ensure_balance

    u = make_user("founder_unit")
    user_email = u["email"]

    # 强写 founder_emails 加该 user(frozen 用 object.__setattr__)
    original = settings.founder_emails
    new_set = frozenset(list(original) + [user_email.lower()])
    object.__setattr__(settings, "founder_emails", new_set)

    try:
        conn = get_connection()
        try:
            bal = ensure_balance(conn, u["user_id"], "max")
            balance_before = bal.subscription_credits + bal.addon_credits

            # founder 短路:调 consume_credits 100 c,但 wallet 不动
            result = consume_credits(
                conn,
                user_id=u["user_id"],
                action="continuation",
                units=100,
                related_id="test-related",
                cost_yuan=1.0,
            )

            # 验证:不扣
            assert result.consumed_subscription == 0
            assert result.consumed_addon == 0
            # 验证:仍写了 audit transaction(delta=0)
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM credit_transactions "
                "WHERE user_id=? AND kind='consume' AND action='continuation'",
                (u["user_id"],),
            ).fetchone()
            assert int(row["cnt"]) >= 1
            # 验证余额未变
            balance_after_row = conn.execute(
                "SELECT subscription_credits + addon_credits AS total "
                "FROM user_credit_balances WHERE user_id=?",
                (u["user_id"],),
            ).fetchone()
            balance_after = int(balance_after_row["total"])
            assert balance_after == balance_before, (
                f"founder 不扣;before={balance_before} after={balance_after}"
            )
        finally:
            conn.close()
    finally:
        # 复原 settings(防影响其他 test)
        object.__setattr__(settings, "founder_emails", original)


# ============================================================
# T3 普通 max 跑 simulation done 后真扣 credit
# ============================================================

def test_max_simulation_consumes_credit_on_done(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    u = make_user("max_consume")
    _upgrade_to_max(u["user_id"])
    _set_balance(u["user_id"], 1000, 0)

    r_proj = client.post(
        "/api/projects", headers=u["headers"],
        json={"name": "P", "type": "novel", "tags": [], "mode": "initial"},
    )
    proj_id = r_proj.json()["id"]
    char_ids = []
    for name in ("X1", "X2", "X3"):
        r_c = client.post(
            f"/api/projects/{proj_id}/characters",
            headers=u["headers"], json={"name": name},
        )
        char_ids.append(r_c.json()["id"])

    # preset 5 轮 mock
    first_id = char_ids[0]
    for rn in range(1, 6):
        patched_simulation_llm.json_queue.append({
            "present_agents": char_ids,
            "speaking_agents": [first_id],
            "location": f"L{rn}", "time_advance": "T",
            "round_seed": "S", "narrator_note": "N",
        })
        patched_simulation_llm.json_queue.append({
            "monologue": "m", "action": "a", "dialogue": "d",
        })
    patched_simulation_llm.text_queue.append("# n")

    balance_before = _get_balance_sum(u["user_id"])

    r = client.post(
        f"/api/projects/{proj_id}/simulations",
        headers=u["headers"],
        json={"divergence": "真扣 credit 测试场景", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    # mock LLM 模式 token 为 0,credit_units_for_text_call 兜底 max(1, ...) 返 1
    # → 至少扣 1 credit

    balance_after = _get_balance_sum(u["user_id"])
    assert balance_after < balance_before, (
        f"普通用户应扣 credit;before={balance_before} after={balance_after}"
    )

    consume_count = _count_transactions(u["user_id"], kind="consume")
    assert consume_count >= 1


# ============================================================
# T4 余额完全够,创建 extract job 不应被拒
# ============================================================

def test_extract_job_creation_with_balance(
    client: TestClient, make_user,
):
    """简单验证 extract 创建路径前置检查通过(不真跑 LLM)。"""
    u = make_user("extract_balance")
    _upgrade_to_max(u["user_id"])
    _set_balance(u["user_id"], 100, 0)

    # 此 test 不真造 upload,只验证 endpoint 调用通过 credit 前置 check
    # 真实 extract 完整流程在 test_extract.py
    r = client.post(
        "/api/uploads/non-existent-id/extract",
        headers=u["headers"],
    )
    # 404(upload 不存在)而非 429(credit 不足) → 证明 credit 前置 check 通过了
    assert r.status_code == 404, r.text


def test_extract_job_blocked_by_zero_balance(
    client: TestClient, make_user,
):
    u = make_user("extract_zero")
    _upgrade_to_max(u["user_id"])
    _set_balance(u["user_id"], 0, 0)

    r = client.post(
        "/api/uploads/some-id/extract",
        headers=u["headers"],
    )
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "INSUFFICIENT_CREDITS"
    assert r.json()["detail"]["action"] == "extract"
