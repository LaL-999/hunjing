"""Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角识别 + 续写一致性测试。

覆盖:
  1. project PATCH narrative_pov(用户手动改)— 4 枚举 + null 清空
  2. project Pydantic Literal 拦截非法值(枚举外的字符串)
  3. consistency_checker._check_narrative_pov_drift —
     - first 模式 + 叙述层无"我" → critical 漂移
     - third 模式 + 叙述层 ≥ 3 个"我" → critical 漂移
     - 对话引号内"我"不算漂移(_strip_dialogue_quotes 剥离)
     - mixed / null → 跳过检测,不产生违规
  4. llm_extract._merge_graphs 聚合 meta.narrative_pov(投票)
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# A. project PATCH narrative_pov
# ============================================================

def test_patch_narrative_pov_first(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    r = client.patch(
        f"/api/projects/{p['id']}", headers=h,
        json={"narrative_pov": "first"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["narrative_pov"] == "first"


def test_patch_narrative_pov_clear_to_null(client: TestClient, make_user):
    """先设成 third,再传 null 清空。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    client.patch(
        f"/api/projects/{p['id']}", headers=h,
        json={"narrative_pov": "third"},
    )
    r = client.patch(
        f"/api/projects/{p['id']}", headers=h,
        json={"narrative_pov": None},
    )
    assert r.status_code == 200, r.text
    assert r.json()["narrative_pov"] is None


def test_patch_narrative_pov_invalid_enum_rejected(client: TestClient, make_user):
    """Pydantic Literal 拦截非法枚举值,422。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    r = client.patch(
        f"/api/projects/{p['id']}", headers=h,
        json={"narrative_pov": "fourth"},
    )
    assert r.status_code == 422


# ============================================================
# B. consistency_checker._check_narrative_pov_drift
# ============================================================

def test_pov_drift_first_with_no_wo_pronoun_triggers_critical():
    """第一人称项目 + 叙述层没出现"我" → critical 漂移违规。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = "渡边走进了那家咖啡店。他点了一杯黑咖啡,坐在窗边的位置。直子的样子又浮现在他脑海里,她总是穿着白色的衣服,笑容浅浅的。" * 5
    violations = _check_narrative_pov_drift(seg, "first", agents=[])
    assert len(violations) == 1
    assert violations[0].severity == "critical"
    assert violations[0].category == "narrative_pov_drift"


def test_pov_drift_third_with_many_wo_pronoun_triggers_critical():
    """第三人称项目 + 叙述层出现 ≥ 3 个"我" → critical 漂移违规。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = "我走进了那家咖啡店。我点了一杯黑咖啡,坐在窗边。我想起了直子,她总是穿着白色的衣服。" * 3
    violations = _check_narrative_pov_drift(seg, "third", agents=[])
    assert len(violations) == 1
    assert violations[0].severity == "critical"


def test_pov_drift_dialogue_quoted_wo_does_not_count_as_drift():
    """第三人称项目 + 叙述层无"我",但对话里有"我" → 不算漂移(对话被剥离)。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = (
        '渡边走进了那家咖啡店。直子坐在窗边,看着他笑了笑。'
        '"我等你很久了,"她说。'
        '"我也想你了。"他答道。'
        '渡边在她对面坐下,点了一杯黑咖啡。'
    ) * 3
    violations = _check_narrative_pov_drift(seg, "third", agents=[])
    # 对话里的"我"被 _strip_dialogue_quotes 剥离,叙述层"我"出现 0 次 → 无漂移
    assert len(violations) == 0


def test_pov_drift_mixed_skips_check():
    """mixed 模式跳过检测,不产生违规。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = "我走进咖啡店。" * 30
    violations = _check_narrative_pov_drift(seg, "mixed", agents=[])
    assert len(violations) == 0


def test_pov_drift_null_skips_check():
    """null 模式跳过检测,不产生违规。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = "渡边走进咖啡店。" * 30
    violations = _check_narrative_pov_drift(seg, None, agents=[])
    assert len(violations) == 0


def test_pov_drift_short_segment_skips_check():
    """短段落(< 100 字)跳过检测,避免统计无意义。"""
    from app.services.consistency_checker import _check_narrative_pov_drift

    seg = "短段落不检测。"
    violations = _check_narrative_pov_drift(seg, "first", agents=[])
    assert len(violations) == 0


# ============================================================
# C. llm_extract._merge_graphs 聚合 meta.narrative_pov 投票
# ============================================================

def test_merge_chunks_agree_on_pov_first():
    """所有 chunk 都报 first → 聚合后 first。"""
    from app.services.llm_extract import _merge_graphs

    chunks = [
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
    ]
    merged = _merge_graphs(chunks)
    assert merged["meta"]["narrative_pov"] == "first"


def test_merge_chunks_majority_wins():
    """3/5 chunks 报 third + 2 报 first → 投票 third(60% 达标)。"""
    from app.services.llm_extract import _merge_graphs

    chunks = [
        {"entities": [], "relations": [], "meta": {"narrative_pov": "third"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "third"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "third"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
    ]
    merged = _merge_graphs(chunks)
    assert merged["meta"]["narrative_pov"] == "third"


def test_merge_chunks_no_clear_majority_falls_back_to_mixed():
    """split 50/50 → mixed(避免误判主导视角)。"""
    from app.services.llm_extract import _merge_graphs

    chunks = [
        {"entities": [], "relations": [], "meta": {"narrative_pov": "first"}},
        {"entities": [], "relations": [], "meta": {"narrative_pov": "third"}},
    ]
    merged = _merge_graphs(chunks)
    assert merged["meta"]["narrative_pov"] == "mixed"


def test_merge_chunks_no_pov_field_returns_none():
    """所有 chunk 都没 meta.narrative_pov → 聚合返 None(老 LLM 输出兼容)。"""
    from app.services.llm_extract import _merge_graphs

    chunks = [
        {"entities": [], "relations": []},
        {"entities": [], "relations": []},
    ]
    merged = _merge_graphs(chunks)
    assert merged["meta"]["narrative_pov"] is None
