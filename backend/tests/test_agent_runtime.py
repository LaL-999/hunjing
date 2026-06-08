"""Sprint 6.A2 M3.A(2026-05-18)— Agent Runtime + 数据层测试。

覆盖:
  - migration 038 字段默认值 + 老 sim 兼容(mode 默认 quick)
  - record_memory 落库正确性 + memory_type 校验
  - share_witnessed_memory 信息不对称严格守护(speaker 自己不复制 / reflection 不外溢)
  - load_agent_context 拉档案 + 私有记忆 + relationship_phases
  - scene_index 切片(up_to_scene_index 过滤)
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient


def _create_project_with_chars(
    client: TestClient, headers: dict, names: list[str],
) -> tuple[str, dict[str, str]]:
    """造项目 + 角色,返回 (project_id, {name: char_id})。"""
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "M3.A 测试", "type": "novel", "tags": []},
    )
    assert p.status_code == 201
    pid = p.json()["id"]
    name_to_id: dict[str, str] = {}
    for n in names:
        r = client.post(
            f"/api/projects/{pid}/characters", headers=headers,
            json={"name": n, "identity": f"{n} 的身份描述"},
        )
        name_to_id[n] = r.json()["id"]
    return pid, name_to_id


def _create_sim_directly(
    project_id: str, user_id: str, *,
    mode: str = "quick",
) -> str:
    """直接 SQL 造一个 simulation(跳过 quota / business validation)。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    sim_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        now = iso_now()
        conn.execute(
            """INSERT INTO simulations
                (id, project_id, user_id, divergence, reshape_percent,
                 rounds_planned, target_chars, style, custom_style_hint,
                 context_simulation_ids, narrative_summary, characters_snapshot,
                 state, current_round, timeline_json, narrative,
                 tokens_input, tokens_output, cost_yuan, error_message,
                 created_at, started_at, completed_at, mode)
               VALUES (?, ?, ?, '测试推演', 50,
                       3, 2000, 'A', NULL,
                       '[]', NULL, '[]',
                       'queued', 0, NULL, NULL,
                       0, 0, 0.0, NULL,
                       ?, NULL, NULL, ?)""",
            (sim_id, project_id, user_id, now, mode),
        )
        conn.commit()
    finally:
        conn.close()
    return sim_id


# ============================================================
# Schema + Model: simulations.mode 默认 quick + 老兼容
# ============================================================

def test_simulation_mode_defaults_quick(client: TestClient, make_user):
    """新建 sim 时不传 mode → DB 默认 'quick';model.from_row 读出 mode='quick'。"""
    u = make_user("mode_default")
    pid, _ = _create_project_with_chars(
        client, u["headers"], ["测试角色1", "测试角色2", "测试角色3"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"])

    from app.db import get_connection
    from app.models.simulation import Simulation
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM simulations WHERE id=?", (sim_id,)
        ).fetchone()
        sim = Simulation.from_row(row)
        assert sim.mode == "quick"
    finally:
        conn.close()


def test_simulation_mode_evolution_persists(client: TestClient, make_user):
    """mode='evolution' 显式落库后,from_row 能读回。"""
    u = make_user("mode_evol")
    pid, _ = _create_project_with_chars(
        client, u["headers"], ["A", "B", "C"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")

    from app.db import get_connection
    from app.models.simulation import Simulation
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM simulations WHERE id=?", (sim_id,)
        ).fetchone()
        sim = Simulation.from_row(row)
        assert sim.mode == "evolution"
    finally:
        conn.close()


# ============================================================
# record_memory + load_agent_context 基础
# ============================================================

def test_record_and_load_agent_memory(client: TestClient, make_user):
    """落 3 条记忆 (reflection / dialogue / action) → load_agent_context 拉回 3 条按 scene_index 升序。"""
    u = make_user("record_load")
    pid, chars = _create_project_with_chars(
        client, u["headers"], ["林黛玉", "紫鹃", "贾宝玉"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")
    daiyu_id = chars["林黛玉"]

    from app.db import get_connection
    from app.services.agent_runtime import (
        load_agent_context, record_memory, list_agent_memories,
    )
    conn = get_connection()
    try:
        # 落 3 条
        record_memory(
            conn, simulation_id=sim_id, character_id=daiyu_id,
            scene_index=0, memory_type="reflection",
            content="独自一人在潇湘馆,想起父亲的话...",
        )
        record_memory(
            conn, simulation_id=sim_id, character_id=daiyu_id,
            scene_index=0, memory_type="dialogue",
            content="紫鹃,把窗子关上。",
        )
        record_memory(
            conn, simulation_id=sim_id, character_id=daiyu_id,
            scene_index=1, memory_type="action",
            content="掩面咳嗽数声。",
        )

        # 拉
        all_mems = list_agent_memories(conn, sim_id, daiyu_id)
        assert len(all_mems) == 3
        assert all_mems[0].scene_index == 0
        assert all_mems[2].scene_index == 1

        # load_agent_context 集成测试
        ctx = load_agent_context(conn, sim_id, daiyu_id)
        assert ctx.character_name == "林黛玉"
        assert ctx.identity == "林黛玉 的身份描述"
        assert len(ctx.memories) == 3
        # 默认非主角(测试创建时未触发 protag_judger)
        assert ctx.is_protagonist is False
    finally:
        conn.close()


def test_load_agent_context_up_to_scene_filter(
    client: TestClient, make_user,
):
    """up_to_scene_index=2 → 只取 scene_index < 2 的记忆(0 + 1 各 1 条 = 2 条)。"""
    u = make_user("scene_filter")
    pid, chars = _create_project_with_chars(
        client, u["headers"], ["A", "B", "C"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")
    a_id = chars["A"]

    from app.db import get_connection
    from app.services.agent_runtime import load_agent_context, record_memory
    conn = get_connection()
    try:
        # 4 幕,每幕 1 条
        for si in range(4):
            record_memory(
                conn, simulation_id=sim_id, character_id=a_id,
                scene_index=si, memory_type="reflection",
                content=f"幕 {si} 的内心独白",
            )

        ctx = load_agent_context(conn, sim_id, a_id, up_to_scene_index=2)
        assert len(ctx.memories) == 2   # 只 scene_index 0 + 1
        for m in ctx.memories:
            assert m.scene_index < 2
    finally:
        conn.close()


# ============================================================
# share_witnessed_memory — 信息不对称严格守护
# ============================================================

def test_share_witnessed_memory_excludes_speaker(
    client: TestClient, make_user,
):
    """speaker 已有自己 dialogue 记忆;share_witnessed 不应给 speaker 复制。
    witnesses 列表里若混入 speaker_id,应被跳过。"""
    u = make_user("witness_no_self")
    pid, chars = _create_project_with_chars(
        client, u["headers"], ["甲", "乙", "丙"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")
    a_id, b_id, c_id = chars["甲"], chars["乙"], chars["丙"]

    from app.db import get_connection
    from app.services.agent_runtime import (
        count_agent_memories, share_witnessed_memory,
    )
    conn = get_connection()
    try:
        # 假装甲说了一句对白(自己 dialogue 应该单独 record),
        # 现在分享给在场的乙、丙、(误传)甲
        written = share_witnessed_memory(
            conn, simulation_id=sim_id,
            speaker_character_id=a_id,
            witnesses_character_ids=[a_id, b_id, c_id],   # 故意混入 speaker
            scene_index=0,
            original_memory_type="dialogue",
            content="今日的酒席倒还热闹。",
        )
        # 写入 2 条(乙、丙),不写甲自己
        assert written == 2

        assert count_agent_memories(conn, sim_id, a_id) == 0    # 甲不应有 witnessed
        assert count_agent_memories(conn, sim_id, b_id) == 1
        assert count_agent_memories(conn, sim_id, c_id) == 1
    finally:
        conn.close()


def test_reflection_does_not_propagate_as_witnessed(
    client: TestClient, make_user,
):
    """reflection 是私有心理活动,绝不外溢成他人的 witnessed(铁律)。"""
    u = make_user("reflection_private")
    pid, chars = _create_project_with_chars(
        client, u["headers"], ["甲", "乙", "丙"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")

    from app.db import get_connection
    from app.services.agent_runtime import (
        count_agent_memories, share_witnessed_memory,
    )
    conn = get_connection()
    try:
        written = share_witnessed_memory(
            conn, simulation_id=sim_id,
            speaker_character_id=chars["甲"],
            witnesses_character_ids=[chars["乙"], chars["丙"]],
            scene_index=0,
            original_memory_type="reflection",   # ← 关键
            content="甲在心里想:这事不对劲。",
        )
        assert written == 0
        assert count_agent_memories(conn, sim_id, chars["乙"]) == 0
        assert count_agent_memories(conn, sim_id, chars["丙"]) == 0
    finally:
        conn.close()


def test_load_agent_context_includes_relationship_phases(
    client: TestClient, make_user,
):
    """relationship + current_phase 加载到 ctx.relationship_phases 字典里。"""
    u = make_user("ctx_phase")
    pid, chars = _create_project_with_chars(
        client, u["headers"], ["张凡", "莫晴雨", "刘美佳"],
    )
    sim_id = _create_sim_directly(pid, u["user_id"], mode="evolution")

    # 造 1 条关系 + 1 个 phase
    rel = client.post(
        f"/api/projects/{pid}/relationships", headers=u["headers"],
        json={
            "source_id": chars["张凡"],
            "target_id": chars["莫晴雨"],
            "type": "情侣",
            "description": "高中同学",
            "strength": "moderate",
        },
    )
    rel_id = rel.json()["id"]
    # 触发 phase 自动迁移(GET phases → auto migrate phase[0])
    client.get(f"/api/relationships/{rel_id}/phases", headers=u["headers"])

    from app.db import get_connection
    from app.services.agent_runtime import load_agent_context
    conn = get_connection()
    try:
        ctx = load_agent_context(conn, sim_id, chars["张凡"])
        assert chars["莫晴雨"] in ctx.relationship_phases
        info = ctx.relationship_phases[chars["莫晴雨"]]
        assert info["name"] == "莫晴雨"
        assert info["phase_type"] == "情侣"
        # 没和刘美佳建关系,不应出现
        assert chars["刘美佳"] not in ctx.relationship_phases
    finally:
        conn.close()
