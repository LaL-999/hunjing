"""Sprint 6.A2 M3.B(2026-05-18)— 灵魂续写主循环 (Agent Evolution Engine) 测试。

覆盖:
  - run_evolution_simulation happy path: N 幕全跑完 + state=done + simulation_scenes 落库
  - scene_picker LLM 输出 + 多样性铁律守护(后端硬兜底,LLM 违反时矫正)
  - dialogue 多轮 + record_memory + share_witnessed_memory 正确写入
  - 信息不对称:agent A 拿不到 agent B 私密 reflection,只能拿到 dialogue/action witnessed
  - narrator 合稿 + simulation_scenes.narrative_segment 落库
  - simulation_service.run_simulation 按 sim.mode 路由(quick 走老路径 / evolution 走新)
"""
from __future__ import annotations

import json
import uuid

import pytest


# ============================================================
# helpers
# ============================================================

def _seed_project_with_chars(n_chars: int = 3) -> tuple[str, str, dict[str, str]]:
    """造 user + project + characters,返回 (user_id, project_id, {name: char_id})。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    now = iso_now()
    names = ["林黛玉", "紫鹃", "贾宝玉", "贾母", "王熙凤"][:n_chars]
    name_to_id: dict[str, str] = {}

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO users (id, email, plan, created_at, updated_at)
               VALUES (?, ?, 'free', ?, ?)""",
            (user_id, f"{user_id[:8]}@test.local", now, now),
        )
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, tags, mode,
                                       created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, '测试 M3.B', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
            (pid, user_id, now, now),
        )
        for n in names:
            cid = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO characters
                    (id, project_id, name, identity, personality, quotes, no_go_list,
                     position_x, position_y, position_z, color,
                     created_at, updated_at,
                     is_protagonist, protagonist_score, protagonist_reasons_json,
                     protagonist_user_pinned)
                   VALUES (?, ?, ?, ?, ?, '[]', '[]', 0, 0, 0, NULL, ?, ?,
                           1, 0.9, '[]', 0)""",
                (cid, pid, n, f"{n} 的身份", f"{n} 的性格", now, now),
            )
            name_to_id[n] = cid
        conn.commit()
    finally:
        conn.close()
    return user_id, pid, name_to_id


def _create_sim(
    user_id: str, project_id: str,
    mode: str = "evolution",
    rounds_planned: int = 2,
) -> str:
    """直接插一行 simulation,跳 quota / validation。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    sim_id = uuid.uuid4().hex
    now = iso_now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO simulations
                (id, project_id, user_id, divergence, reshape_percent,
                 rounds_planned, target_chars, style, custom_style_hint,
                 context_simulation_ids, narrative_summary, characters_snapshot,
                 state, current_round, timeline_json, narrative,
                 tokens_input, tokens_output, cost_yuan, error_message,
                 created_at, started_at, completed_at, mode)
               VALUES (?, ?, ?, '测试推演', 50,
                       ?, 2000, 'A', NULL,
                       '[]', NULL, '[]',
                       'queued', 0, NULL, NULL,
                       0, 0, 0.0, NULL,
                       ?, NULL, NULL, ?)""",
            (sim_id, project_id, user_id, rounds_planned, now, mode),
        )
        conn.commit()
    finally:
        conn.close()
    return sim_id


class FakeCounter:
    """LLM 调用计数器 — 跟踪不同 prompt 类型的调用次数。"""
    def __init__(self):
        self.scene_picker = 0
        self.agent_dialogue = 0
        self.narrator = 0
        # Sprint 6.A2 M4.1 新增
        self.world_state_extractor = 0
        self.plot_tracker = 0
        # Sprint 6.A2 M4.3 新增
        self.consistency_checker = 0
        # Sprint 6.A2 M5 新增
        self.entity_registrar = 0
        self.action_extractor = 0
        self.emotional_state_tracker = 0
        self.temporal_lock = 0


def _fake_llm_factory(counter: FakeCounter, scene_names: list[str] | None = None):
    """生成 mock call_llm_json:按 system_prompt 内容判断是哪个 LLM 调用,返回桩。

    Sprint 6.A2 M4.1(2026-05-19)扩展:识别 m4_world_state_extractor 和 m4_plot_tracker,
    回归零事实 / 零 thread mock 让 happy path 测试不受影响。
    """
    scene_names = scene_names or ["潇湘馆", "荣禧堂", "大观园"]

    def _fake(system_prompt, user_input, **kwargs):
        usage = {"input_tokens": 100, "output_tokens": 50}
        # M4.3 关键修复:用 prompt 标题(# XXX —)精确匹配,
        # 因为 narrator prompt 内容里提到"一致性自检员"(retry_hint section),
        # 旧的 `if "一致性自检员" in system_prompt` 会误匹配 narrator 调用
        # M5.3 升级:由于硬铁律 prepend 到顶部,system_prompt 不一定以 "# 标题" 开头
        # 改成在前 1000 字内查找标题
        # 2026-06-02 hotfix:Patch D/E/F 加了多个 hard_constraints sections
        # (走向终章强化 / 明示陌生关系 / 时空过渡 等)→ prepend 段可能超 1500 字
        # 改成不限 prefix 长度,直接全文搜 "以下是原 prompt:"
        sp = system_prompt.strip()

        # M5.3 prepend 段含 "以下是原 prompt:" — 之后是真实 prompt 标题
        if "以下是原 prompt:" in sp:
            after = sp.split("以下是原 prompt:", 1)[1]
            real_start = after[:500]
        else:
            real_start = sp[:2000]

        def has(s):
            return s in real_start

        if has("# 场景调度员"):
            idx = counter.scene_picker % len(scene_names)
            counter.scene_picker += 1
            return ({
                "scene_name": scene_names[idx],
                "scene_source": "project_scenes_pick",
                "time_anchor": "深夜",
                "reasoning": "测试 mock",
            }, usage)
        elif has("# 世界事实账本"):
            counter.world_state_extractor += 1
            return ({"new_facts": []}, usage)
        elif has("# 主线追踪员"):
            counter.plot_tracker += 1
            return ({
                "resolved_thread_ids": [],
                "advanced_thread_ids": [],
                "new_threads": [],
            }, usage)
        elif has("# 一致性自检员"):
            counter.consistency_checker += 1
            return ({"violations": []}, usage)
        elif has("# 实体身份注册员"):
            counter.entity_registrar += 1
            return ({"new_entities": [], "alias_additions": []}, usage)
        elif has("# 原子动作提取员"):
            counter.action_extractor += 1
            return ({"actions": []}, usage)
        elif has("# 角色情绪追踪员"):
            counter.emotional_state_tracker += 1
            return ({"emotional_states": []}, usage)
        elif "时间锚解析器" in real_start[:200]:
            counter.temporal_lock += 1
            return ({"new_offset_hours": 0.0}, usage)
        elif has("# Agent 对话") or "扮演一个角色" in real_start[:400]:
            counter.agent_dialogue += 1
            speaker = user_input.get("name", "未知")
            return ({
                "reflection": f"{speaker} 的内心独白(mock)",
                "dialogue": f"{speaker} 说:这是测试对白。",
                "action": "",
            }, usage)
        elif has("# 叙述者"):
            counter.narrator += 1
            # 2026-06-01:mock 输出按调用编号差异化,避免 P0Q.1 / P0T.1 复读检测对相同输出
            # 触发 retry(scene 0/1 用同一 mock 文本会被检测为复读 → 测试 9 == 6 失败)
            seg_id = counter.narrator
            return ({
                "narrative_segment": (
                    f"深夜片段-{seg_id}:屋外蝉声渐弱,室内烛影微动。林黛玉抬眸看了紫鹃一眼,"
                    f"低声道,这一夜也是奇了-{seg_id}。紫鹃轻轻应了一声,垂下眼睫。"
                    f"窗外远处偶有夜鸟掠过-{seg_id},庭中花影斑驳。这一幕氛围沉静,"
                    f"人物心思各异,各自沉浸于无声的思忖之中(mock 合稿 200+ 字填充-{seg_id})。"
                ),
            }, usage)
        else:
            raise ValueError(f"未识别的 system_prompt(前 100 字):{system_prompt[:100]!r}")
    return _fake


def _patch_all_evolution_llms(monkeypatch, fake) -> None:
    """同时 patch 8 个会调 call_llm_json 的模块:
       - agent_evolution_engine(scene_picker / agent_dialogue / narrator)
       - world_state_extractor(M4.1 抽事实)
       - plot_tracker(M4.1 追主线)
       - consistency_checker(M4.3 Reflexion 自检)
       - entity_registrar(M5.1 实体注册)
       - action_extractor(M5.2 动作流水)
       - emotional_state_tracker(M5.6 情绪链)
       - temporal_lock(M5.5 时间锁,LLM 解析时间锚)

    一行调用,避免每个测试重复 8 个 setattr。
    """
    targets = [
        "app.services.agent_evolution_engine.call_llm_json",
        "app.services.world_state_extractor.call_llm_json",
        "app.services.plot_tracker.call_llm_json",
        "app.services.consistency_checker.call_llm_json",
        "app.services.entity_registrar.call_llm_json",
        "app.services.action_extractor.call_llm_json",
        "app.services.emotional_state_tracker.call_llm_json",
        "app.services.temporal_lock.call_llm_json",
    ]
    for t in targets:
        monkeypatch.setattr(t, fake)


# ============================================================
# happy path
# ============================================================

def test_run_evolution_happy_path(monkeypatch):
    """跑完 2 幕灵魂续写 → state=done + 2 个 simulation_scenes 行 + narrative 非空。"""
    user_id, pid, chars = _seed_project_with_chars(n_chars=3)
    sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=2)

    counter = FakeCounter()
    fake = _fake_llm_factory(counter, scene_names=["场所A", "场所B"])
    # M4.1:同时 patch 3 个 LLM 入口(agent_evolution / world_state / plot_tracker)
    _patch_all_evolution_llms(monkeypatch, fake)

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 验证
    from app.db import get_connection
    conn = get_connection()
    try:
        sim_row = conn.execute(
            "SELECT * FROM simulations WHERE id=?", (sim_id,)
        ).fetchone()
        assert sim_row["state"] == "done"
        assert sim_row["narrative"] and len(sim_row["narrative"]) > 100

        scene_rows = conn.execute(
            "SELECT * FROM simulation_scenes WHERE simulation_id=? ORDER BY scene_index",
            (sim_id,),
        ).fetchall()
        assert len(scene_rows) == 2
        assert scene_rows[0]["scene_index"] == 0
        assert scene_rows[1]["scene_index"] == 1
        # 多样性铁律:2 幕场景名不同(我们 mock 了 A 和 B 轮转)
        assert scene_rows[0]["scene_name"] != scene_rows[1]["scene_name"]
        # narrative_segment 非空
        for r in scene_rows:
            assert r["narrative_segment"], f"scene {r['scene_index']} segment empty"
    finally:
        conn.close()

    # LLM 调用次数
    assert counter.scene_picker == 2   # 2 幕
    # dialogue: 3 agents × DIALOGUE_ROUNDS_PER_SCENE (=3) × 2 scenes = 18
    from app.services.agent_evolution_engine import DIALOGUE_ROUNDS_PER_SCENE
    assert counter.agent_dialogue == 3 * DIALOGUE_ROUNDS_PER_SCENE * 2
    # M5.4 multi-sample voting:每幕 narrator 调 3 次(3 个温度候选)+ 可能 retry
    # 2 幕 × 3 候选 = 6 次基础(retry 在最优样本仍 critical 时触发,每 retry +1 调用)
    # 2026-06-01:mock 输出含相同人物 / 主题词,会被程序级检测器(P0Q.1 对白复读 /
    # P0T.1 叙述句复读 / P0V.3 主题词刷屏)对 scene_index>0 判 critical 触发 retry.
    # 允许 retry 路径(最多 3 retry × 1 单温度 = +3 次),断言改为基础调用数下限.
    assert 6 <= counter.narrator <= 6 + 3 * 2, (
        f"narrator 应 6 次基础 + 至多 6 次 retry,实际 {counter.narrator}"
    )


def test_diversity_rule_backend_enforces_when_llm_violates(monkeypatch):
    """LLM 违反多样性铁律(连续 3 幕同场景),后端硬矫正到不同场景。"""
    user_id, pid, chars = _seed_project_with_chars(n_chars=3)
    sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=3)

    # 先 seed 一个 project_scenes 候选(让 backend 矫正时有可选目标)
    from app.db import get_connection
    from app.services.project_service import iso_now
    conn = get_connection()
    try:
        for nm in ["A 场所", "B 场所"]:
            conn.execute(
                """INSERT INTO project_scenes
                    (id, project_id, name, aliases_json, description,
                     appearance_chunk_count, created_at, updated_at)
                   VALUES (?, ?, ?, '[]', '', 10, ?, ?)""",
                (uuid.uuid4().hex, pid, nm, iso_now(), iso_now()),
            )
        conn.commit()
    finally:
        conn.close()

    # LLM 故意每次都说 "A 场所"(违反多样性)
    def evil_fake(system_prompt, user_input, **kwargs):
        usage = {"input_tokens": 50, "output_tokens": 20}
        if "场景调度员" in system_prompt:
            return ({
                "scene_name": "A 场所",
                "scene_source": "project_scenes_pick",
                "time_anchor": "深夜",
                "reasoning": "evil mock",
            }, usage)
        elif "扮演一个角色" in system_prompt:
            return ({"reflection": "...", "dialogue": "对白", "action": ""}, usage)
        elif "叙述者" in system_prompt:
            return ({"narrative_segment": "x" * 200}, usage)
        raise ValueError("bad")

    _patch_all_evolution_llms(monkeypatch, evil_fake)

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    conn = get_connection()
    try:
        scenes = conn.execute(
            "SELECT scene_name FROM simulation_scenes WHERE simulation_id=? ORDER BY scene_index",
            (sim_id,),
        ).fetchall()
        names = [r["scene_name"] for r in scenes]
        # 至少第 3 幕(scene_index=2)不应再是 "A 场所"(被后端矫正)
        # 前 2 幕可以连续 A,但第 3 幕 recent=["A 场所","A 场所"],应被矫正成 B 场所
        assert names[0] == "A 场所"
        assert names[1] == "A 场所"
        assert names[2] != "A 场所", f"第 3 幕未矫正:{names}"
    finally:
        conn.close()


def test_information_asymmetry_witnessed_only(monkeypatch):
    """信息不对称:agent A 不会拿到 agent B 的 reflection,只会拿到 dialogue/action 的 witnessed 副本。"""
    user_id, pid, chars = _seed_project_with_chars(n_chars=3)
    sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=1)

    counter = FakeCounter()
    _patch_all_evolution_llms(
        monkeypatch,
        _fake_llm_factory(counter, scene_names=["唯一场景"]),
    )

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    from app.db import get_connection
    from app.services.agent_runtime import list_agent_memories
    conn = get_connection()
    try:
        # 拿每个 agent 的所有 memory,检查类型分布
        for name, cid in chars.items():
            mems = list_agent_memories(conn, sim_id, cid)
            types = {m.memory_type for m in mems}
            # 每个 agent 至少有自己的 reflection / dialogue / action 之一
            # witnessed 是别人 dialogue/action 的副本(他们应该有)
            # 关键:reflection 类型的 content 不应该出现在其他 agent 的 memories 里

        # 抽 1 个 agent 的 reflection,检查没出现在他人的 memories.content 里
        from app.models.agent_private_memory import AgentPrivateMemory
        daiyu_mems = list_agent_memories(conn, sim_id, chars["林黛玉"])
        daiyu_reflections = {
            m.content for m in daiyu_mems if m.memory_type == "reflection"
        }
        assert daiyu_reflections, "林黛玉应至少有 1 条 reflection"

        for other_name, other_cid in chars.items():
            if other_name == "林黛玉":
                continue
            other_mems = list_agent_memories(conn, sim_id, other_cid)
            other_contents = {m.content for m in other_mems}
            leaked = daiyu_reflections & other_contents
            assert not leaked, (
                f"信息不对称违反:林黛玉 reflection 泄漏到 {other_name}: {leaked}"
            )
    finally:
        conn.close()


def test_simulation_service_routes_by_mode(monkeypatch):
    """simulation_service.run_simulation 按 sim.mode 路由:
    'evolution' → 调 agent_evolution_engine.run_evolution_simulation
    'quick'     → 调 _run_simulation_inner(老路径)
    """
    # 创建 2 个 sim — quick + evolution
    user_id, pid, chars = _seed_project_with_chars(n_chars=3)
    quick_sim_id = _create_sim(user_id, pid, mode="quick", rounds_planned=1)
    evolution_sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=1)

    called_quick = {"n": 0}
    called_evolution = {"n": 0}

    def fake_quick(conn, sim_id):
        called_quick["n"] += 1

    def fake_evolution(sim_id):
        called_evolution["n"] += 1

    monkeypatch.setattr(
        "app.services.simulation_service._run_simulation_inner", fake_quick,
    )
    monkeypatch.setattr(
        "app.services.agent_evolution_engine.run_evolution_simulation",
        fake_evolution,
    )

    from app.services.simulation_service import run_simulation
    run_simulation(quick_sim_id)
    run_simulation(evolution_sim_id)

    assert called_quick["n"] == 1, "quick 路径应被调"
    assert called_evolution["n"] == 1, "evolution 路径应被调"


def test_no_agents_available_skips_scene(monkeypatch):
    """项目无角色 → 召唤 0 agent → 该幕 skip + emit evolution_scene_skipped。"""
    # 0 角色项目
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    now = iso_now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO users (id, email, plan, created_at, updated_at)
               VALUES (?, ?, 'free', ?, ?)""",
            (user_id, f"{user_id[:8]}@test.local", now, now),
        )
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, tags, mode,
                                       created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, '空项目', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
            (pid, user_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=1)

    counter = FakeCounter()
    _patch_all_evolution_llms(monkeypatch, _fake_llm_factory(counter))

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    conn = get_connection()
    try:
        sim_row = conn.execute(
            "SELECT state, narrative FROM simulations WHERE id=?", (sim_id,)
        ).fetchone()
        # 跑完(0 角色就跳过所有幕)— 仍正常结束
        assert sim_row["state"] == "done"
        scene_count = conn.execute(
            "SELECT COUNT(*) AS c FROM simulation_scenes WHERE simulation_id=?",
            (sim_id,),
        ).fetchone()["c"]
        # scene_picker 会跑(LLM mock),但 summoner 召不到 agent → 不落 simulation_scenes 行
        assert scene_count == 0
    finally:
        conn.close()


def test_narrator_segment_persisted_to_simulation_scenes(monkeypatch):
    """narrator 合稿后,simulation_scenes.narrative_segment 应非空。"""
    user_id, pid, chars = _seed_project_with_chars(n_chars=2)
    sim_id = _create_sim(user_id, pid, mode="evolution", rounds_planned=1)

    counter = FakeCounter()
    _patch_all_evolution_llms(monkeypatch, _fake_llm_factory(counter))

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT narrative_segment FROM simulation_scenes "
            "WHERE simulation_id=? AND scene_index=0",
            (sim_id,),
        ).fetchone()
        assert row is not None
        assert row["narrative_segment"]
        assert len(row["narrative_segment"]) >= 50
    finally:
        conn.close()
