"""inheritance_depth + chain_context_builder 父辈方向回归测试(2026-06-02 hotfix).

bug 历史:
  - context_simulation_ids 按时序 ASC 排:[最远祖先, ..., 直接父]
  - 原实现取 parents[0](误以为是直接父),实际是最远祖先
  - 后果:
    (1) compute_inheritance_metadata_for_user → depth 算少 1,chip 显示错代数
    (2) chain_context_builder._build_ancestor_summaries_section → 灵魂续写 prior 跳代

  hotfix:两处都改为 parents[-1].
"""
from __future__ import annotations

import uuid

import pytest

from app.models.simulation import Simulation
from app.services.simulation_service import compute_inheritance_metadata_for_user


def _make_sim(
    *,
    sim_id: str,
    context: list[str] | None = None,
    divergence: str = "test",
) -> Simulation:
    return Simulation(
        id=sim_id,
        project_id="proj1",
        user_id="user1",
        divergence=divergence,
        reshape_percent=10,
        rounds_planned=5,
        target_chars=5000,
        style="A",
        custom_style_hint=None,
        context_simulation_ids=context or [],
        narrative_summary=None,
        characters_snapshot=[],
        state="done",
        current_round=5,
        timeline=None,
        narrative="",
        tokens_input=0,
        tokens_output=0,
        cost_yuan=0.0,
        error_message=None,
        created_at="2026-06-02T00:00:00",
        started_at=None,
        completed_at=None,
    )


class TestInheritanceDepthRespectsDirectParent:
    """直接父 = context_simulation_ids 数组**最后一个**(数组按时序 ASC)."""

    def test_independent_sim_depth_is_zero(self):
        sims = [_make_sim(sim_id="s1", context=[])]
        meta = compute_inheritance_metadata_for_user(sims)
        assert meta["s1"]["depth"] == 0
        assert meta["s1"]["ancestors_chain"] == []

    def test_direct_child_of_root_depth_is_one(self):
        """简单单链:s1(root) → s2."""
        s1 = _make_sim(sim_id="s1", context=[])
        s2 = _make_sim(sim_id="s2", context=["s1"])
        meta = compute_inheritance_metadata_for_user([s1, s2])
        assert meta["s2"]["depth"] == 1
        assert len(meta["s2"]["ancestors_chain"]) == 1
        assert meta["s2"]["ancestors_chain"][0]["id"] == "s1"

    def test_two_layer_chain_with_full_ancestors_array(self):
        """关键 hotfix 场景:s3.context = [s1(根), s2(直接父)] — 数组按时序 ASC.

        bug 前:parents[0] = s1 → s3 的父被算成 s1 → depth = 1 ✗
        bug 后:parents[-1] = s2 → s3 的父正确算成 s2 → depth = 2 ✓
        """
        s1 = _make_sim(sim_id="s1", context=[])
        s2 = _make_sim(sim_id="s2", context=["s1"])
        # 用户基于 s2 续写时,前端"祖先链锁定"自动展开成 [s1, s2]
        s3 = _make_sim(sim_id="s3", context=["s1", "s2"])
        meta = compute_inheritance_metadata_for_user([s1, s2, s3])
        # s3 的祖先链应该是 [s1, s2],depth = 2(不是 1!)
        assert meta["s3"]["depth"] == 2, (
            f"s3 应为 depth=2(基于 s2 续写,完整链 s1→s2→s3),"
            f"实际 {meta['s3']['depth']}"
        )
        ancestors = meta["s3"]["ancestors_chain"]
        assert len(ancestors) == 2
        # 祖先链按 depth ASC(根在前)
        assert ancestors[0]["id"] == "s1"
        assert ancestors[0]["depth"] == 0
        assert ancestors[1]["id"] == "s2"
        assert ancestors[1]["depth"] == 1

    def test_three_layer_chain_full_ancestors_array(self):
        """3 层链:s1 → s2 → s3 → s4,s4.context = [s1, s2, s3]."""
        s1 = _make_sim(sim_id="s1", context=[])
        s2 = _make_sim(sim_id="s2", context=["s1"])
        s3 = _make_sim(sim_id="s3", context=["s1", "s2"])
        s4 = _make_sim(sim_id="s4", context=["s1", "s2", "s3"])
        meta = compute_inheritance_metadata_for_user([s1, s2, s3, s4])
        assert meta["s4"]["depth"] == 3
        ancestors = meta["s4"]["ancestors_chain"]
        assert [a["id"] for a in ancestors] == ["s1", "s2", "s3"]
        assert [a["depth"] for a in ancestors] == [0, 1, 2]

    def test_sibling_branches_have_same_depth(self):
        """姐妹分支(同代):s1 → s2a / s2b,两条都是 depth=1."""
        s1 = _make_sim(sim_id="s1", context=[])
        s2a = _make_sim(sim_id="s2a", context=["s1"])
        s2b = _make_sim(sim_id="s2b", context=["s1"])
        meta = compute_inheritance_metadata_for_user([s1, s2a, s2b])
        assert meta["s2a"]["depth"] == 1
        assert meta["s2b"]["depth"] == 1

    def test_legacy_single_parent_still_works(self):
        """老 sim 只有 1 个 parent(没经过祖先链锁定 patch),应仍正确."""
        s1 = _make_sim(sim_id="s1", context=[])
        s2 = _make_sim(sim_id="s2", context=["s1"])
        # s3 的 context 只有 s2(老格式 — 没自动展开成 [s1, s2])
        s3 = _make_sim(sim_id="s3", context=["s2"])
        meta = compute_inheritance_metadata_for_user([s1, s2, s3])
        # s3 → s2 → s1,链长 2 = depth 2
        assert meta["s3"]["depth"] == 2
        assert [a["id"] for a in meta["s3"]["ancestors_chain"]] == ["s1", "s2"]

    def test_missing_ancestor_placeholder(self):
        """父辈在 id_map 不存在(已删 / 跨项目)— 占位."""
        # s2 引用了 deleted_id(不在 sims 列表里)
        s2 = _make_sim(sim_id="s2", context=["deleted_id"])
        meta = compute_inheritance_metadata_for_user([s2])
        assert meta["s2"]["depth"] == 1
        assert meta["s2"]["ancestors_chain"][0]["id"] == "deleted_id"
        assert meta["s2"]["ancestors_chain"][0]["divergence_short"] == "(前作已删除)"
