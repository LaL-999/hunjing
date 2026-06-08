"""快速模式反复读检测测试(2026-06-02).

覆盖:
  - WORD_FAMILIES 各族识别
  - 阈值按字数浮动(短篇 4 / 中长 6 / 长篇 9)
  - 单短语 > 2 次也触发(即使族级未超)
  - retry hint 文本格式
"""
from __future__ import annotations

import pytest

from app.services.quick_repetition_guard import (
    SPECIFIC_PHRASE_MAX,
    WORD_FAMILIES,
    RepetitionViolation,
    build_retry_hint_from_violations,
    check_full_narrative_repetition,
)


# ============================================================
# 基础族识别
# ============================================================

class TestWordFamilyDetection:
    def test_throat_family_over_threshold_triggers(self):
        """短篇内"喉结上下滚动"重复 6 次 → 触发(阈值 4)."""
        # 构造 ~3000 字短篇文本,加入 6 次喉结相关
        body = "走廊空旷。" * 400  # ~3200 字背景
        body += "他喉结上下滚动。" * 3
        body += "他咽了口唾沫。" * 3
        narrative = body
        violations = check_full_narrative_repetition(narrative)
        # 应触发"喉咙紧绷"族
        family_names = [v.family for v in violations]
        assert "喉咙紧绷" in family_names

    def test_hand_tension_family_triggers(self):
        body = "夜色深沉。" * 400
        body += "她指节发白。" * 3
        body += "她攥紧拳头。" * 3
        violations = check_full_narrative_repetition(body)
        assert "手部紧张" in [v.family for v in violations]

    def test_below_threshold_no_violation(self):
        """族级 ≤ 阈值 4 不触发(短篇)."""
        body = "走廊空旷。" * 400
        body += "他喉结上下滚动。"  # 1 次
        body += "她咽了口唾沫。"  # 1 次
        violations = check_full_narrative_repetition(body)
        family_names = [v.family for v in violations]
        assert "喉咙紧绷" not in family_names

    def test_single_phrase_over_2_triggers_even_if_family_low(self):
        """单短语 > 2 次也触发(即使族级总数低)."""
        body = "走廊空旷。" * 400
        body += "他喉结上下滚动。" * 3  # 3 次同一短语 — 超 SPECIFIC_PHRASE_MAX=2
        violations = check_full_narrative_repetition(body)
        family_names = [v.family for v in violations]
        assert "喉咙紧绷" in family_names


class TestThresholdByLength:
    def test_short_text_threshold_is_4(self):
        # 短篇 < 6000 字阈值 = 4
        short_body = "他喉结上下滚动。" * 5  # 5 次,字数 ~75
        # 太短,需要凑够 200 字才进入检测
        padding = "走廊空旷无人。" * 30
        body = padding + ("他喉结上下滚动。" * 5)
        violations = check_full_narrative_repetition(body)
        assert any(v.family == "喉咙紧绷" for v in violations)

    def test_medium_text_threshold_is_6(self):
        # 中篇 6000-15000 字阈值 6;5 次同族不触发
        body = "走廊空旷。" * 1100  # ~8800 字背景
        body += "他喉结上下滚动一下。"  # 1 次(单短语 = 1,不超 2)
        body += "她咽了口唾沫。"  # 1 次
        body += "嗓子干哑。"  # 1 次
        body += "喉间发涩。"  # 1 次
        body += "他咽口水。" * 0  # 0
        # 族级合计 4 次 < 阈值 6 → 不应触发
        violations = check_full_narrative_repetition(body)
        family_names = [v.family for v in violations]
        # 多样化 ≤ 4 不超中篇阈值
        # 注:由于"咽口水"也可能被部分匹配,允许灵活
        # 关键:每个单短语都 = 1 次,不会超 SPECIFIC_PHRASE_MAX=2
        # 但族级 = 4 < 6,不触发族级阈值
        assert "喉咙紧绷" not in family_names


class TestEdgeCases:
    def test_empty_returns_empty(self):
        assert check_full_narrative_repetition("") == []
        assert check_full_narrative_repetition("短") == []

    def test_below_200_chars_returns_empty(self):
        body = "他喉结上下滚动。" * 10  # 10 次但总字数 < 200 / 大约 150 字
        assert check_full_narrative_repetition(body) == []

    def test_normal_diverse_text_no_violation(self):
        """多样化文本(每段不同意象)不触发."""
        # 注意:不能 * 20 整段重复,会让短语反复
        # 这里用 200 段不同句子(用 index 让短语不重复)
        sentences = []
        scenes = [
            "图书馆角落",
            "教室窗边",
            "操场跑道",
            "宿舍走廊",
            "食堂排队",
            "公交站台",
            "便利店门口",
            "地铁站台",
            "校门口梧桐树下",
            "天台铁丝网边",
        ]
        for i in range(60):
            scene = scenes[i % len(scenes)]
            sentences.append(f"{scene}的下午第 {i} 个瞬间。背景声平和。")
        body = "".join(sentences)
        violations = check_full_narrative_repetition(body)
        # 此文本不出现任何高频族短语 → 应无违规
        assert violations == []


# ============================================================
# retry hint 生成
# ============================================================

class TestRetryHintBuild:
    def test_empty_violations_returns_empty_hint(self):
        assert build_retry_hint_from_violations([]) == ""

    def test_hint_contains_family_and_count(self):
        violations = [
            RepetitionViolation(
                family="喉咙紧绷",
                total_count=10,
                threshold=4,
                top_phrases=[("喉结上下滚动", 7), ("咽了口唾沫", 3)],
                severity="critical",
            ),
        ]
        hint = build_retry_hint_from_violations(violations)
        assert "喉咙紧绷" in hint
        assert "10" in hint  # total_count
        assert "4" in hint   # threshold
        assert "喉结上下滚动" in hint
        assert "重写要求" in hint
        assert "替代库" in hint


# ============================================================
# 用户报告场景重现:网恋风云 4520 字片段
# ============================================================

class TestUserReportedScenario:
    def test_real_user_report_triggers_multiple_families(self):
        """用户给的《网恋风云》节选 — 应触发多族超频."""
        # 模拟该文本结构:5000 字内 喉结滚 10+ / 指尖发抖 15+ / 路灯昏黄 6
        body = "走廊里的灯光彻底暗下来。" * 50  # ~750 字背景
        # 喉咙紧绷族 10 次
        body += (
            "他喉结上下滚动一下。"      # 1
            "他喉结上下滚动一下。"      # 2
            "李爽喉结上下滚动。"        # 3
            "他咽了口唾沫。"            # 1
            "嗓子干哑。"                # 1
            "喉间发涩。"                # 1
            "他喉结上下滚动一下。"      # 4
            "陈绮喉咙发紧。"            # 1
        )
        # 手部紧张族 8 次
        body += (
            "她指节发白。" * 3
            + "她攥紧拳头。" * 2
            + "他指甲掐进掌心。" * 3
        )
        # 夜晚路灯族 5 次
        body += (
            "路灯昏黄。" * 3
            + "夜风卷起碎纸片。" * 2
        )
        violations = check_full_narrative_repetition(body)
        family_names = [v.family for v in violations]
        # 三族都应被识别
        assert "喉咙紧绷" in family_names
        assert "手部紧张" in family_names
        assert "夜晚路灯" in family_names

        # 验证 retry hint 能生成
        hint = build_retry_hint_from_violations(violations)
        assert len(hint) > 100  # 非空非短
        assert "喉咙紧绷" in hint
        assert "手部紧张" in hint


# ============================================================
# WORD_FAMILIES 字典自检
# ============================================================

class TestWordFamiliesIntegrity:
    def test_all_families_have_phrases(self):
        for family, phrases in WORD_FAMILIES.items():
            assert len(phrases) >= 3, f"{family} 短语库过少({len(phrases)}个),应至少 3 个"

    def test_no_duplicate_phrases_across_families(self):
        all_phrases: dict[str, str] = {}
        for family, phrases in WORD_FAMILIES.items():
            for ph in phrases:
                if ph in all_phrases:
                    pytest.fail(
                        f"短语 '{ph}' 同时出现在 {all_phrases[ph]} 和 {family}"
                    )
                all_phrases[ph] = family
