"""阶段 8.5 — model_comparison_service 单元测试。

策略:mock `_openai_compat_call_json` 为不同的"假 LLM",验证:
  1. <2 providers → ComparisonError
  2. screenplay 不存在 / 跨用户 → ComparisonError
  3. 场景 ID 不存在 → ComparisonError
  4. 2 providers 都成功 → 双候选 + recommended 是分高的那个
  5. 1 provider 失败 + 1 成功 → 失败的 success=False + error,成功的入 candidates
     (graceful degradation 铁律)
  6. 2 providers 都失败 → 都 success=False / recommended=None
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import yaml as yamllib

from app.db import get_connection
from app.screenplay.services import model_comparison_service
from app.screenplay.services.model_comparison_service import (
    ProviderConfig,
    compare_scene_extraction,
)
from app.services.llm_client import LlmCallFailed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_user(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, f"{uid[:6]}@cmp-test.com", "free", _now(), _now()),
    )
    return uid


def _make_novel(conn, user_id: str) -> str:
    nid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_novels (id, user_id, title, source_format, "
        "source_filename, total_chars, total_chapters, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (nid, user_id, "t", "txt", "t.txt", 0, 0, _now()),
    )
    return nid


def _build_screenplay_yaml() -> str:
    """造一个有 1 场 + 2 角色 + 4 elements 的 yaml。"""
    full = {
        "meta": {"schema_version": "1.0", "title": "测试"},
        "characters": [
            {"id": "char_001", "name": "林墨", "aka": []},
            {"id": "char_002", "name": "苏清", "aka": []},
        ],
        "locations": [
            {"id": "loc_001", "name": "县医院", "int_ext": "INT"},
        ],
        "scenes": [
            {
                "id": "scene_001",
                "number": 1,
                "heading": {"int_ext": "INT", "location_id": "loc_001", "time_of_day": "夜"},
                "summary": "林墨与苏清相遇",
                "characters_present": ["char_001", "char_002"],
                "source": {"chapter": 1, "paragraph_range": [1, 5]},
                "elements": [
                    {"type": "action", "text": "林墨走进病房。"},
                    {"type": "dialogue", "character_id": "char_001", "text": "苏清?"},
                    {"type": "dialogue", "character_id": "char_002", "text": "你来了。"},
                    {"type": "voiceover", "character_id": "char_001", "text": "她真的还在这里。"},
                ],
            },
        ],
    }
    return yamllib.dump(full, allow_unicode=True)


def _make_screenplay(conn, novel_id: str) -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_screenplays (id, novel_id, yaml_text, stats_json, "
        "warnings_json, failed_chapters_json, schema_version, model_name, "
        "created_at, optimization_origin) "
        "VALUES (?, ?, ?, '{}', '[]', '[]', '1.0', NULL, ?, 'initial')",
        (sid, novel_id, _build_screenplay_yaml(), _now()),
    )
    return sid


def _good_provider(label: str) -> ProviderConfig:
    return ProviderConfig(
        label=label,
        api_key="sk-test-key",
        base_url="https://api.test.com/v1",
        model=f"{label.lower()}-test",
    )


def _make_llm_mock(
    elements_by_label: dict[str, list[dict]] | None = None,
    raises_by_label: dict[str, Exception] | None = None,
):
    """造一个 _openai_compat_call_json 的 mock,按 vendor_label 决定返什么。"""
    elements_by_label = elements_by_label or {}
    raises_by_label = raises_by_label or {}

    def fake(system_prompt, user_input, *, api_key, api_base, model, vendor_label,
             max_tokens=4000, temperature=0.6, retries=2, timeout=60.0,
             frequency_penalty=0.0, presence_penalty=0.0):
        # vendor_label 形如 "COMPARE-DeepSeek V3"
        label = vendor_label.removeprefix("COMPARE-")
        if label in raises_by_label:
            raise raises_by_label[label]
        elements = elements_by_label.get(label, [])
        return {"elements": elements}, {"input_tokens": 100, "output_tokens": 50}

    return fake


# ============================================================
# 测试
# ============================================================


def _run(coro):
    """父平台测试套件没装 pytest-asyncio,asyncio.run 即可。"""
    return asyncio.run(coro)


class TestModelComparison:
    def test_fewer_than_2_providers_raises(self):
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            sid = _make_screenplay(conn, nid)
            conn.commit()
            with pytest.raises(
                model_comparison_service.ComparisonError,
                match="至少需要 2 个 provider",
            ):
                _run(compare_scene_extraction(
                    sid, uid, scene_id="scene_001",
                    providers=[_good_provider("OnlyOne")],
                ))
        finally:
            conn.close()

    def test_screenplay_not_exist_raises(self):
        with pytest.raises(
            model_comparison_service.ComparisonError,
            match="剧本不存在",
        ):
            _run(compare_scene_extraction(
                "nonexistent_screenplay", "ghost_user",
                scene_id="scene_001",
                providers=[_good_provider("A"), _good_provider("B")],
            ))

    def test_cross_user_raises(self):
        conn = get_connection()
        try:
            user_a = _make_user(conn)
            user_b = _make_user(conn)
            novel_b = _make_novel(conn, user_b)
            sid_b = _make_screenplay(conn, novel_b)
            conn.commit()
            with pytest.raises(
                model_comparison_service.ComparisonError,
                match="剧本不存在",
            ):
                _run(compare_scene_extraction(
                    sid_b, user_a, scene_id="scene_001",
                    providers=[_good_provider("A"), _good_provider("B")],
                ))
        finally:
            conn.close()

    def test_scene_not_exist_raises(self):
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            sid = _make_screenplay(conn, nid)
            conn.commit()
            with pytest.raises(
                model_comparison_service.ComparisonError,
                match="场景.*不存在",
            ):
                _run(compare_scene_extraction(
                    sid, uid, scene_id="scene_999",
                    providers=[_good_provider("A"), _good_provider("B")],
                ))
        finally:
            conn.close()

    def test_two_providers_both_succeed_recommended_is_higher(self):
        """两 provider 都成功 → recommended 是分高的那个。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            sid = _make_screenplay(conn, nid)
            conn.commit()

            # A 抽 4 个 elements(对白覆盖好) / B 只抽 1 个(对白覆盖差)
            good_elements_a = [
                {"type": "action", "text": "林墨缓步走进病房,目光扫过床边。"},
                {"type": "dialogue", "character_name": "林墨", "text": "苏清,我来看你了。"},
                {"type": "dialogue", "character_name": "苏清", "text": "你终于来了。"},
                {"type": "action", "text": "林墨握住苏清的手,两人沉默良久。"},
            ]
            poor_elements_b = [
                {"type": "action", "text": "他们见面。"},  # 单元素低分
            ]
            mock_fn = _make_llm_mock({
                "Provider A": good_elements_a,
                "Provider B": poor_elements_b,
            })

            with patch(
                "app.screenplay.services.model_comparison_service._openai_compat_call_json",
                side_effect=mock_fn,
            ):
                result = _run(compare_scene_extraction(
                    sid, uid, scene_id="scene_001",
                    providers=[
                        _good_provider("Provider A"),
                        _good_provider("Provider B"),
                    ],
                ))

            assert result.scene_id == "scene_001"
            assert len(result.candidates) == 2
            cand_a = next(c for c in result.candidates if c.provider_label == "Provider A")
            cand_b = next(c for c in result.candidates if c.provider_label == "Provider B")
            assert cand_a.success
            assert cand_b.success
            assert cand_a.scores is not None
            assert cand_b.scores is not None
            # A 应该分高(对白更全 + 元素更多)
            assert cand_a.scores.overall > cand_b.scores.overall
            assert result.recommended_label == "Provider A"
        finally:
            conn.close()

    def test_one_provider_fails_other_still_returned(self):
        """1 个 provider 抛 LlmCallFailed,另一个成功 — 都进 candidates(graceful)。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            sid = _make_screenplay(conn, nid)
            conn.commit()

            good_elements = [
                {"type": "action", "text": "林墨走进病房。"},
                {"type": "dialogue", "character_name": "林墨", "text": "苏清?"},
            ]
            mock_fn = _make_llm_mock(
                elements_by_label={"Good": good_elements},
                raises_by_label={"Bad": LlmCallFailed("假装 API key 无效")},
            )

            with patch(
                "app.screenplay.services.model_comparison_service._openai_compat_call_json",
                side_effect=mock_fn,
            ):
                result = _run(compare_scene_extraction(
                    sid, uid, scene_id="scene_001",
                    providers=[_good_provider("Good"), _good_provider("Bad")],
                ))

            assert len(result.candidates) == 2
            good = next(c for c in result.candidates if c.provider_label == "Good")
            bad = next(c for c in result.candidates if c.provider_label == "Bad")
            assert good.success
            assert good.scores is not None
            assert not bad.success
            assert "假装 API key 无效" in bad.error_message
            # recommended 是 Good(只有它成功)
            assert result.recommended_label == "Good"
        finally:
            conn.close()

    def test_all_providers_fail_recommended_is_none(self):
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            sid = _make_screenplay(conn, nid)
            conn.commit()

            mock_fn = _make_llm_mock(raises_by_label={
                "A": LlmCallFailed("network 1"),
                "B": LlmCallFailed("network 2"),
            })

            with patch(
                "app.screenplay.services.model_comparison_service._openai_compat_call_json",
                side_effect=mock_fn,
            ):
                result = _run(compare_scene_extraction(
                    sid, uid, scene_id="scene_001",
                    providers=[_good_provider("A"), _good_provider("B")],
                ))

            assert all(not c.success for c in result.candidates)
            assert result.recommended_label is None
        finally:
            conn.close()
