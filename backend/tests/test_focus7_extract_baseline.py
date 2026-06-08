"""Sprint 6.A2 FOCUS.7(2026-05-22):首次抽取写入 behavior_baseline 4 维测试。

LLM 在 character_generator profile 中输出 behavior_baseline 后,
extract_service._extract_behavior_baseline_from_profile 校验枚举/范围,
INSERT characters 时写入 behavior_baseline_json 列。
"""
from __future__ import annotations


# ============================================================
# A. _extract_behavior_baseline_from_profile 单元测试
# ============================================================

def test_extract_valid_baseline():
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {
        "behavior_baseline": {
            "speech_register": "平和",
            "emotional_intensity": 5,
            "moral_compass": "灰",
            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
    }
    result = _extract_behavior_baseline_from_profile(profile)
    assert result is not None
    assert result["speech_register"] == "平和"
    assert result["emotional_intensity"] == 5
    assert result["moral_compass"] == "灰"


def test_extract_invalid_speech_register_to_none():
    """LLM 输出非枚举值(如'中等')→ speech_register 置 None,不抛。"""
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {
        "behavior_baseline": {
            "speech_register": "中等",   # 非枚举
            "emotional_intensity": 5,
            "moral_compass": "善",
            "out_of_baseline_examples": [],
        },
    }
    result = _extract_behavior_baseline_from_profile(profile)
    assert result is not None
    assert result["speech_register"] is None
    assert result["emotional_intensity"] == 5
    assert result["moral_compass"] == "善"


def test_extract_emotional_intensity_out_of_range_to_none():
    """超 1-10 范围 → None。"""
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {
        "behavior_baseline": {
            "speech_register": "强硬",
            "emotional_intensity": 11,   # 超上限
            "moral_compass": None,
            "out_of_baseline_examples": [],
        },
    }
    result = _extract_behavior_baseline_from_profile(profile)
    assert result is not None
    assert result["emotional_intensity"] is None
    assert result["speech_register"] == "强硬"


def test_extract_all_invalid_returns_none():
    """4 维全非法 / 全空 → 整个 baseline 返 None(对齐"老数据"语义)。"""
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {
        "behavior_baseline": {
            "speech_register": "中等",
            "emotional_intensity": 99,
            "moral_compass": "中立",
            "out_of_baseline_examples": "不是 list",
        },
    }
    result = _extract_behavior_baseline_from_profile(profile)
    assert result is None


def test_extract_missing_baseline_field_returns_none():
    """profile 没有 behavior_baseline 字段 → None。"""
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {"identity": "...", "personality": "..."}
    assert _extract_behavior_baseline_from_profile(profile) is None


# P0G.2(2026-05-24):删除 test_extract_out_of_baseline_examples_filter_non_str
# 字段 out_of_baseline_examples 已永久删除,该 case 测的功能不存在了


def test_extract_boolean_not_treated_as_intensity():
    """LLM 误输出 True/False(在 Python 是 int 子类)→ 不当作 intensity。"""
    from app.services.extract_service import _extract_behavior_baseline_from_profile
    profile = {
        "behavior_baseline": {
            "speech_register": "平和",
            "emotional_intensity": True,   # bool 不应该被当成 1
            "moral_compass": None,
            "out_of_baseline_examples": [],
        },
    }
    result = _extract_behavior_baseline_from_profile(profile)
    assert result is not None
    assert result["emotional_intensity"] is None
