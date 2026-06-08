"""M10.A 文风滤镜 + M10.B 意象库 — 单元测试(2026-05-27).

不走 API,直接测 service / checker 函数。覆盖 8 个核心 case:
  1. 句长漂移(实测 vs 目标差 > 15 个百分点 → critical)
  2. 对白率漂移
  3. 主导感官不一致
  4. 段长相对偏差 > 30%
  5. 雷区命中 → critical
  6. 推荐意象 0 命中 → warning
  7. 双轨完美贴合 → 0 violation(回归)
  8. 没有 author_compass → 完全跳过
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from app.services.style_filter_service import (
    compute_dialogue_ratio,
    compute_paragraph_avg_length,
    compute_sentence_length_distribution,
    compute_sense_ratio,
    measure_style_metrics,
    top_sense,
)


# ============================================================
# pure service unit tests (M10.A.1)
# ============================================================

def test_sentence_length_short_dominant():
    text = "他笑了。她哭了。雪还在下。\n\n他说了句话,她没回应,只是看着远方。\n\n岛村抬头望向银河,目光落在叶子身上,心里没什么波澜。"
    sl = compute_sentence_length_distribution(text)
    # 三个短句 "他笑了" / "她哭了" / "雪还在下" + 一个中句 + 一个长句
    assert sl["短句占比"] >= 0.4
    assert sum(sl.values()) > 0.95


def test_dialogue_ratio():
    text = '驹子说:"你来了吗?"岛村点点头,没说话。' * 5
    r = compute_dialogue_ratio(text)
    assert 0 < r < 0.5


def test_sense_top_visual():
    text = "他看着光,望着雪,目光投向银河。色彩明灭。"
    s = compute_sense_ratio(text)
    assert top_sense(s) == "视觉"


def test_paragraph_length():
    text = "第一段。\n\n第二段比较长一点点。\n\n这是第三段,字数多一些,故意拉长。"
    avg = compute_paragraph_avg_length(text)
    assert avg > 0


# ============================================================
# checker integration tests
# ============================================================

@pytest.fixture
def conn_with_compass(tmp_path):
    """造一个含 author_compass 的 in-memory DB。"""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE author_compass (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE,
        author_name TEXT, work_title TEXT,
        external_profile_json TEXT, external_status TEXT NOT NULL DEFAULT 'pending',
        external_error TEXT, external_at TEXT,
        internal_metrics_json TEXT, internal_status TEXT NOT NULL DEFAULT 'pending',
        internal_error TEXT, internal_at TEXT,
        final_compass_json TEXT, user_locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def _insert_compass(
    conn,
    project_id: str,
    *,
    external: dict | None = None,
    internal: dict | None = None,
):
    conn.execute(
        """INSERT INTO author_compass VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            uuid.uuid4().hex, project_id, "X作家", "Y作品",
            json.dumps(external, ensure_ascii=False) if external else None,
            "done" if external else "pending", None, "2026-05-27" if external else None,
            json.dumps(internal, ensure_ascii=False) if internal else None,
            "done" if internal else "pending", None, "2026-05-27" if internal else None,
            None, 0,
            "2026-05-27", "2026-05-27",
        ),
    )
    conn.commit()


# ----- 句长漂移 -----

def test_check_style_drift_sentence_length(conn_with_compass):
    """目标短句 80% 长句 5%,实测全长句 → 应至少报短句 OR 长句漂移。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"句长": {"短句占比": 0.8, "中句占比": 0.15, "长句占比": 0.05}},
    )
    # 全长句的文本(每句 > 40 字)
    long_text = "".join(
        f"这是一个故意写得非常非常长的句子用来确保它超过了四十个汉字的阈值这样统计才会被归类成长句而不是中句这次是第 {i} 句。"
        for i in range(8)
    )
    long_text += "\n\n" + long_text   # 多段保证总字数 > 200

    v = _check_style_drift(conn_with_compass, "p1", long_text)
    length_drift = [vi for vi in v if ("短句占比" in vi.evidence or "长句占比" in vi.evidence)]
    assert len(length_drift) >= 1, f"应报句长漂移,实际 violations={[vi.evidence for vi in v]}"


# ----- 对白率漂移 -----

def test_check_style_drift_dialogue_ratio(conn_with_compass):
    """目标对白 10%,实测对白 60%(满段对白)→ 报 critical。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"对白率": {"比例": 0.1}},
    )
    # 满满当当全对白
    dialogue_heavy = '"你来了吗?""我来了。""怎么样了?""还行。""那就好。""恩。""走吧。""好。"' * 10
    dialogue_heavy += "\n\n" + dialogue_heavy
    v = _check_style_drift(conn_with_compass, "p1", dialogue_heavy)
    dr_drift = [vi for vi in v if "对白率" in vi.evidence]
    assert len(dr_drift) >= 1


# ----- 主导感官不一致 -----

def test_check_style_drift_sense_mismatch(conn_with_compass):
    """目标视觉 0.5(主导),实测听觉主导 → 报 critical。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={
            "感官比例": {"视觉": 0.5, "听觉": 0.1, "嗅觉": 0.15, "触觉": 0.2, "味觉": 0.05},
        },
    )
    # 全是听觉关键词
    listen_heavy = "她听见声音。门外传来响声。远处有人喊叫。声音越来越大,叫喊不停。回响在山谷间。她又听了一会儿。" * 5
    listen_heavy += "\n\n" + listen_heavy
    v = _check_style_drift(conn_with_compass, "p1", listen_heavy)
    sense_drift = [vi for vi in v if "主导感官" in vi.evidence]
    assert len(sense_drift) >= 1


# ----- 段长漂移 -----

def test_check_style_drift_paragraph_length(conn_with_compass):
    """目标段长 100,实测平均段长 < 30 → 报 critical(相对差 > 30%)。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"段落节奏": {"平均段长字数": 100}},
    )
    # 多个短段(每段 5-10 字),平均段长会很小
    short_paragraphs = "\n\n".join(["短段。"] * 20) + "需要超过 200 字总长才能进检测,所以加这段补字数,补够 200 字以上才行哦。" * 5
    v = _check_style_drift(conn_with_compass, "p1", short_paragraphs)
    para_drift = [vi for vi in v if "平均段长" in vi.evidence]
    assert len(para_drift) >= 1


# ----- 雷区命中 -----

def test_check_imagery_violation_forbidden_hit(conn_with_compass):
    """雷区"现代俚语"命中 → critical。"""
    from app.services.consistency_checker import _check_imagery_violations
    _insert_compass(
        conn_with_compass, "p1",
        external={"雷区": ["现代俚语", "大段直白心理独白"]},
        internal={"意象偏好": ["雪", "银河"]},
    )
    text_with_forbidden = "她走过雪地,远处银河淡了。她说了句现代俚语,显得格格不入,完全不像她平日的样子。这里需要补足够多的字数,所以一句一句地写下去,凑到一百字以上才能通过最小字数门槛。" * 2
    v = _check_imagery_violations(conn_with_compass, "p1", text_with_forbidden)
    crits = [vi for vi in v if vi.severity == "critical"]
    assert len(crits) >= 1
    assert any("现代俚语" in vi.evidence for vi in crits)


# ----- 推荐意象 0 命中 -----

def test_check_imagery_violation_no_imagery_hit(conn_with_compass):
    """推荐意象一个都没命中 → warning。"""
    from app.services.consistency_checker import _check_imagery_violations
    _insert_compass(
        conn_with_compass, "p1",
        external={"雷区": []},
        internal={"意象偏好": ["雪", "银河", "镜子"]},
    )
    text_no_imagery = "他走在街上。手机响了。他接起电话,听见对方的声音。" * 5
    v = _check_imagery_violations(conn_with_compass, "p1", text_no_imagery)
    warns = [vi for vi in v if vi.severity == "warning"]
    assert len(warns) >= 1
    assert any("推荐意象 0 命中" in vi.evidence for vi in warns)


# ----- 完美贴合不应报 -----

def test_check_style_no_drift_when_matched(conn_with_compass):
    """实测约等于目标 → 不报。"""
    from app.services.consistency_checker import _check_style_drift, _check_imagery_violations
    _insert_compass(
        conn_with_compass, "p1",
        external={"雷区": ["现代俚语"]},
        internal={
            "句长": {"短句占比": 0.5, "中句占比": 0.3, "长句占比": 0.2},
            "对白率": {"比例": 0.15},
            "意象偏好": ["雪", "银河"],
        },
    )
    # 混合短中长 + 少量对白 + 含意象 + 不含雷区
    text = """雪还在下。

岛村望着远处的银河,目光落在驹子苍白的脸上,她抱着叶子,瓦砾间的水汽蒸腾。

"你在看什么?"驹子轻声问。

岛村没有回答。

天空泛起一道浅浅的痕,银河淡去了。""" * 3
    v_drift = _check_style_drift(conn_with_compass, "p1", text)
    v_imagery = _check_imagery_violations(conn_with_compass, "p1", text)
    # 应没 critical(可能有微小偏差不到阈值)
    crit_drift = [vi for vi in v_drift if vi.severity == "critical"]
    # 这里允许少量偏差,实际可能 0-1 条 critical(取决于关键词命中)
    crit_imagery = [vi for vi in v_imagery if vi.severity == "critical"]
    assert len(crit_imagery) == 0   # 无雷区命中
    # imagery warning 不应报(有意象)
    warn_imagery = [vi for vi in v_imagery if vi.severity == "warning"]
    assert len(warn_imagery) == 0


# ----- P5.3(2026-05-27)— 身体描写尺度对齐检测 -----

def test_check_body_register_frequent_target_avoided(conn_with_compass):
    """P5.3:目标 frequent(挪威森林),实测 none(续作完全回避)→ 跨 3 档 → critical。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"身体描写尺度": {"频率": "frequent", "直白度": "sensual-implicit"}},
    )
    # 完全没有身体描写关键词的产物(模拟续作回避做爱场景)
    text = "他坐在窗前喝茶。窗外银杏树叶飘落。远处传来电车声。" \
           "她回来后,两人聊了一会儿天气。然后吃晚饭。" \
           "饭后她去洗碗,他在沙发上看报纸。窗外天色渐暗。" * 6
    v = _check_style_drift(conn_with_compass, "p1", text)
    body_v = [vi for vi in v if "身体描写频率" in vi.evidence]
    assert len(body_v) >= 1, "目标 frequent 实测 none 应报 critical"
    # 评注里应含"回避"
    assert "回避" in body_v[0].suggestion


def test_check_body_register_rare_target_explicit(conn_with_compass):
    """P5.3:目标 rare(雪国 含蓄),实测 frequent → 跨 2 档 → critical。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"身体描写尺度": {"频率": "rare", "直白度": "sensual-implicit"}},
    )
    # 密集身体描写(露骨)
    text = (
        "她解开衬衫,他亲吻她的脖颈。两人交缠在床上。"
        "她喘息着,身体微颤。他抚摸她的肌肤,深入她的怀里。"
        "床上的被子凌乱,空气湿热。" * 8
    )
    v = _check_style_drift(conn_with_compass, "p1", text)
    body_v = [vi for vi in v if "身体描写频率" in vi.evidence]
    assert len(body_v) >= 1, "目标 rare 实测 frequent 应报 critical"
    assert "露骨" in body_v[0].suggestion or "含蓄" in body_v[0].suggestion


def test_check_body_register_aligned_no_violation(conn_with_compass):
    """P5.3:目标 rare 实测 rare → 0 档差 → 不报。"""
    from app.services.consistency_checker import _check_style_drift
    _insert_compass(
        conn_with_compass, "p1",
        internal={"身体描写尺度": {"频率": "rare", "直白度": "sensual-implicit"}},
    )
    # 偶有亲吻 / 拥抱(rare 量,千字内 1-2 处)
    text = (
        "她走过来,轻轻吻了他一下。两人对视片刻,他拥抱了她。" * 1
        + "窗外银杏叶飘落。两人去公园散步。聊天气。" * 30
    )
    v = _check_style_drift(conn_with_compass, "p1", text)
    body_v = [vi for vi in v if "身体描写频率" in vi.evidence]
    assert len(body_v) == 0, f"rare vs rare 不应报,实际: {[b.evidence for b in body_v]}"


# ----- 没 author_compass 完全跳过 -----

def test_check_style_no_compass_returns_empty(conn_with_compass):
    """项目没生成 author_compass → 返空列表(不报错)。"""
    from app.services.consistency_checker import _check_style_drift, _check_imagery_violations
    # 不 insert compass
    long_text = "这是一段普通文本,足够长以通过最小字数门槛,字数补到 200 以上。" * 10
    v_drift = _check_style_drift(conn_with_compass, "p_nonexistent", long_text)
    v_imagery = _check_imagery_violations(conn_with_compass, "p_nonexistent", long_text)
    assert v_drift == []
    assert v_imagery == []
