"""Sprint 6.A2 M7.D(2026-05-20)— 接续继承链计算单元测试。

覆盖:
  A. 独立推演:depth=0,chain=[]
  B. 单代接续:depth=1,chain=[原作]
  C. 二代接续:depth=2,chain=[原作, 第 1 代]
  D. 三代接续:depth=3,chain 全链
  E. 链断兜底:parent 不在用户名下 → 占位"(前作已删除)"
  F. 多 parent(line A 主链 + B 副 parent):取第一个 parent 算主链
  G. depth 在 ancestors_chain 节点上正确(0-indexed,父辈 = N-1)
  H. API 端到端:GET /simulations 透出 inheritance_depth + ancestors_chain
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import _connect, transaction, execute as db_execute
from app.models.simulation import Simulation
from app.services.project_service import iso_now
from app.services.simulation_service import (
    compute_inheritance_metadata_for_user,
)


# ============================================================
# helpers — 直接构造 Simulation 对象,跳过 LLM 跑全流程
# ============================================================

def _make_sim(
    sim_id: str,
    divergence: str,
    context_ids: list[str] = None,
    project_id: str = "proj_x",
    user_id: str = "user_x",
) -> Simulation:
    """构造一个最小 Simulation 实例用于测 inheritance 算法。"""
    return Simulation(
        id=sim_id,
        project_id=project_id,
        user_id=user_id,
        divergence=divergence,
        reshape_percent=10,
        rounds_planned=5,
        target_chars=4000,
        style="auto",
        custom_style_hint=None,
        context_simulation_ids=context_ids or [],
        narrative_summary=None,
        characters_snapshot=[],
        timeline=None,
        narrative=None,
        tokens_input=0,
        tokens_output=0,
        cost_yuan=0.0,
        state="done",
        current_round=5,
        error_message=None,
        created_at="2026-05-20T00:00:00Z",
        started_at=None,
        completed_at=None,
        original_tail_excerpt=None,
        mode="initial",
        use_outline_first=0,
    )


def _pair(sim: Simulation, project_name: str = "测试") -> tuple[Simulation, str]:
    return (sim, project_name)


# ============================================================
# A. 独立推演:depth=0
# ============================================================

def test_independent_sim_depth_zero_empty_chain():
    a = _make_sim("a", "原作锚点 1")
    result = compute_inheritance_metadata_for_user([_pair(a)])
    assert result["a"]["depth"] == 0
    assert result["a"]["ancestors_chain"] == []


# ============================================================
# B. 单代接续
# ============================================================

def test_single_generation_chain_depth_one():
    a = _make_sim("a", "原作:浪子初遇剑客")
    b = _make_sim("b", "如果他没有醉酒,而是直问真相", context_ids=["a"])
    result = compute_inheritance_metadata_for_user([_pair(a), _pair(b)])

    assert result["a"]["depth"] == 0
    assert result["a"]["ancestors_chain"] == []

    assert result["b"]["depth"] == 1
    assert len(result["b"]["ancestors_chain"]) == 1
    ancestor = result["b"]["ancestors_chain"][0]
    assert ancestor["id"] == "a"
    assert ancestor["divergence_short"] == "原作:浪子初遇剑客"
    assert ancestor["depth"] == 0


# ============================================================
# C. 二代接续
# ============================================================

def test_second_generation_chain_depth_two():
    a = _make_sim("a", "原作")
    b = _make_sim("b", "第 1 代锚点", context_ids=["a"])
    c = _make_sim("c", "第 2 代锚点", context_ids=["b"])
    result = compute_inheritance_metadata_for_user([_pair(a), _pair(b), _pair(c)])

    assert result["c"]["depth"] == 2
    chain = result["c"]["ancestors_chain"]
    assert len(chain) == 2
    assert chain[0]["id"] == "a" and chain[0]["depth"] == 0
    assert chain[0]["divergence_short"] == "原作"
    assert chain[1]["id"] == "b" and chain[1]["depth"] == 1
    assert chain[1]["divergence_short"] == "第 1 代锚点"


# ============================================================
# D. 三代接续
# ============================================================

def test_third_generation_chain_depth_three():
    a = _make_sim("a", "原作")
    b = _make_sim("b", "第 1 代", context_ids=["a"])
    c = _make_sim("c", "第 2 代", context_ids=["b"])
    d = _make_sim("d", "第 3 代", context_ids=["c"])
    result = compute_inheritance_metadata_for_user(
        [_pair(a), _pair(b), _pair(c), _pair(d)],
    )

    assert result["d"]["depth"] == 3
    chain = result["d"]["ancestors_chain"]
    assert [n["id"] for n in chain] == ["a", "b", "c"]
    assert [n["depth"] for n in chain] == [0, 1, 2]


# ============================================================
# E. 链断兜底:parent 不存在 → "(前作已删除)" 占位
# ============================================================

def test_chain_break_when_parent_not_found():
    """B 接续 A,但 A 已被删 / 跨用户访问不到 → 链中含占位节点。"""
    b = _make_sim("b", "第 1 代锚点", context_ids=["deleted_parent_id"])
    result = compute_inheritance_metadata_for_user([_pair(b)])

    assert result["b"]["depth"] == 1
    chain = result["b"]["ancestors_chain"]
    assert len(chain) == 1
    assert chain[0]["divergence_short"] == "(前作已删除)"
    assert chain[0]["depth"] == 0


# ============================================================
# F. 多 parent:取第一个作主链
# ============================================================

def test_multi_parent_takes_last_as_main_chain():
    """B 同时接续 A1 + A2,**最后一个 parent 作主链**(2026-06-02 hotfix:
    context_simulation_ids 按 created_at ASC 排 → 直接父在 [-1] 不是 [0]).

    旧测试假设 a1 是主链父辈,实际语义是 [a1, a2] 表示"a1 是更早祖先,a2 是直接父辈".
    数组首=最远祖先 / 尾=直接父辈.
    """
    a1 = _make_sim("a1", "原作主分支")
    a2 = _make_sim("a2", "原作旁支")
    b = _make_sim("b", "多源接续", context_ids=["a1", "a2"])
    result = compute_inheritance_metadata_for_user(
        [_pair(a1), _pair(a2), _pair(b)],
    )

    # 2026-06-02 fix:a2 是直接父辈(它本身 depth=0)→ b.depth = 1
    assert result["b"]["depth"] == 1
    chain = result["b"]["ancestors_chain"]
    assert len(chain) == 1
    assert chain[0]["id"] == "a2"     # 最后一个 parent = 直接父辈


# ============================================================
# G. divergence 过长截断到 40 字
# ============================================================

def test_divergence_truncated_to_40_chars():
    long_div = "锚" * 100
    a = _make_sim("a", long_div)
    b = _make_sim("b", "第 1 代", context_ids=["a"])
    result = compute_inheritance_metadata_for_user([_pair(a), _pair(b)])

    ancestor = result["b"]["ancestors_chain"][0]
    assert len(ancestor["divergence_short"]) == 40
    assert ancestor["divergence_short"] == "锚" * 40


# ============================================================
# H. API 端到端 — GET /simulations 透出新字段
# ============================================================

def _insert_sim_row(
    sim_id: str, project_id: str, user_id: str,
    divergence: str, context_ids: list[str] = None,
) -> None:
    """直接 INSERT 一条 sim row(state=done),绕开 LLM 跑流程。"""
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, "
                " rounds_planned, target_chars, style, "
                " context_simulation_ids, characters_snapshot, "
                " state, current_round, narrative, created_at, "
                " tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, ?, 10, 5, 4000, 'auto', ?, '[]', 'done', 5, '...', ?, 0, 0, 0)",
                (
                    sim_id, project_id, user_id, divergence,
                    json.dumps(context_ids or [], ensure_ascii=False),
                    iso_now(),
                ),
            )
    finally:
        conn.close()


def test_get_simulations_api_returns_inheritance_fields(
    client: TestClient, make_user,
):
    """端到端:GET /api/simulations 返回 inheritance_depth + ancestors_chain。"""
    user = make_user("alice")
    h = user["headers"]
    # 造 project
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "继承链测试项目", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    # 造 3 代 sims:a → b → c
    _insert_sim_row("sim_a", project_id, user["user_id"], "原作锚点")
    _insert_sim_row("sim_b", project_id, user["user_id"], "第 1 代锚点", ["sim_a"])
    _insert_sim_row("sim_c", project_id, user["user_id"], "第 2 代锚点", ["sim_b"])

    r = client.get("/api/simulations", headers=h)
    assert r.status_code == 200
    sims = r.json()
    by_id = {s["id"]: s for s in sims}

    # sim_a 独立推演
    assert by_id["sim_a"]["inheritance_depth"] == 0
    assert by_id["sim_a"]["ancestors_chain"] == []

    # sim_b 第 1 代
    assert by_id["sim_b"]["inheritance_depth"] == 1
    b_chain = by_id["sim_b"]["ancestors_chain"]
    assert len(b_chain) == 1
    assert b_chain[0]["id"] == "sim_a"
    assert b_chain[0]["depth"] == 0

    # sim_c 第 2 代
    assert by_id["sim_c"]["inheritance_depth"] == 2
    c_chain = by_id["sim_c"]["ancestors_chain"]
    assert len(c_chain) == 2
    assert c_chain[0]["id"] == "sim_a" and c_chain[0]["depth"] == 0
    assert c_chain[1]["id"] == "sim_b" and c_chain[1]["depth"] == 1
