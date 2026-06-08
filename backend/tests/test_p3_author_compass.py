"""P3 作者指南针 Agent — API + service 测试(2026-05-26).

6 个 case 覆盖:
  1. GET 没记录 → 404 AUTHOR_COMPASS_NOT_FOUND
  2. PUT 没记录 → 404
  3. POST analyze → 创建记录(双轨各跑,LLM mock)
  4. 锁定后 POST analyze → 不重跑(返回锁定值)
  5. PUT user_locked=True 没 final_compass → 422 FINAL_COMPASS_REQUIRED_BEFORE_LOCK
  6. 跨用户隔离 — bob 访问 alice 的 compass → 404
"""
from __future__ import annotations

import json
from unittest.mock import patch


def _create_project(client, headers, name: str = "测试项目") -> str:
    r = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "mode": "initial"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ============================================================
# Case 1 — GET 没记录 → 404
# ============================================================

def test_get_compass_when_not_exists_returns_404(client, make_user):
    alice = make_user("alice")
    proj_id = _create_project(client, alice["headers"])

    r = client.get(
        f"/api/projects/{proj_id}/author-compass",
        headers=alice["headers"],
    )
    assert r.status_code == 404
    body = r.json()
    assert body["detail"]["code"] == "AUTHOR_COMPASS_NOT_FOUND"


# ============================================================
# Case 2 — PUT 没记录 → 404
# ============================================================

def test_put_compass_when_not_exists_returns_404(client, make_user):
    alice = make_user("alice")
    proj_id = _create_project(client, alice["headers"])

    r = client.put(
        f"/api/projects/{proj_id}/author-compass",
        headers=alice["headers"],
        json={"author_name": "川端康成"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AUTHOR_COMPASS_NOT_FOUND"


# ============================================================
# Case 3 — POST analyze 创建记录(LLM mock 成功)
# ============================================================

_FAKE_EXTERNAL_PROFILE = {
    "流派": "物哀",
    "年代": "20 世纪中期",
    "文化背景": "日本战后",
    "主题偏好": ["美的转瞬即逝", "孤独"],
    "风格标签": ["含蓄留白", "诗化叙事"],
    "雷区": ["大段直白心理独白", "现代俚语"],
    "代表手法": ["象征性意象"],
    "致敬作品": [],
}

_FAKE_INTERNAL_METRICS = {
    "句长": {"短句占比": 0.55, "中句占比": 0.30, "长句占比": 0.15, "评注": "短句为主"},
    "对白率": {"比例": 0.20, "评注": "对白偏少"},
    "感官比例": {"视觉": 0.4, "听觉": 0.2, "嗅觉": 0.15, "触觉": 0.2, "味觉": 0.05, "评注": "视觉主导"},
    "段落节奏": {"平均段长字数": 120, "短长段配比": "1:1", "评注": "层次分明"},
    "意象偏好": ["雪", "银河", "镜子"],
    "视角": {"人称": "third", "全知或限知": "limited", "评注": "限知视角"},
    "基调": {"情感色彩": "哀而不伤", "节奏感": "舒缓", "评注": "冷峻含蓄"},
}


def test_post_analyze_creates_compass_with_both_tracks(client, make_user):
    alice = make_user("alice")
    proj_id = _create_project(client, alice["headers"])

    # mock 两轨 LLM 调用
    def fake_call_llm_json(system_prompt, user_input, **kwargs):
        # 用每个 prompt 独有的角色名作为标识(避免 prompt 互相提及对方导致误判)
        if "作家研究员" in system_prompt:
            return _FAKE_EXTERNAL_PROFILE, {"input_tokens": 100, "output_tokens": 200}
        elif "文风量化分析员" in system_prompt:
            return _FAKE_INTERNAL_METRICS, {"input_tokens": 200, "output_tokens": 300}
        raise RuntimeError(f"unexpected prompt: {system_prompt[:80]}")

    # internal 轨需要 upload 文本 — mock 文本采样
    def fake_get_full_text(conn, project_id):
        return "这是一段测试原作文本。" * 100  # ~1000 字

    with patch(
        "app.services.author_compass_service.call_llm_json",
        side_effect=fake_call_llm_json,
    ), patch(
        "app.services.author_compass_service._get_full_text_for_project",
        side_effect=fake_get_full_text,
    ):
        r = client.post(
            f"/api/projects/{proj_id}/author-compass/analyze",
            headers=alice["headers"],
            json={"author_name": "川端康成", "work_title": "雪国"},
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project_id"] == proj_id
    assert body["author_name"] == "川端康成"
    assert body["work_title"] == "雪国"
    assert body["external_status"] == "done"
    assert body["internal_status"] == "done"
    assert body["external_profile"]["流派"] == "物哀"
    assert "雪" in body["internal_metrics"]["意象偏好"]
    assert body["user_locked"] is False


# ============================================================
# Case 4 — 锁定后 POST analyze → 不重跑
# ============================================================

def test_post_analyze_when_locked_does_not_rerun(client, make_user):
    alice = make_user("alice")
    proj_id = _create_project(client, alice["headers"])

    # 先创建一条 compass 并锁定
    call_count = [0]

    def fake_call_llm_json(system_prompt, user_input, **kwargs):
        call_count[0] += 1
        if "作家研究员" in system_prompt:
            return _FAKE_EXTERNAL_PROFILE, {"input_tokens": 0, "output_tokens": 0}
        if "文风量化分析员" in system_prompt:
            return _FAKE_INTERNAL_METRICS, {"input_tokens": 0, "output_tokens": 0}
        raise RuntimeError("unknown prompt")

    with patch(
        "app.services.author_compass_service.call_llm_json",
        side_effect=fake_call_llm_json,
    ), patch(
        "app.services.author_compass_service._get_full_text_for_project",
        return_value="测试" * 500,
    ):
        # 第一次 analyze:跑 2 次 LLM
        client.post(
            f"/api/projects/{proj_id}/author-compass/analyze",
            headers=alice["headers"],
            json={"author_name": "X", "work_title": "Y"},
        )
        first_call_count = call_count[0]
        assert first_call_count == 2

        # 用户锁定
        r_lock = client.put(
            f"/api/projects/{proj_id}/author-compass",
            headers=alice["headers"],
            json={
                "final_compass": {"merged": "user-final"},
                "user_locked": True,
            },
        )
        assert r_lock.status_code == 200, r_lock.text
        assert r_lock.json()["user_locked"] is True

        # 第二次 analyze:不应再调 LLM(锁定)
        r2 = client.post(
            f"/api/projects/{proj_id}/author-compass/analyze",
            headers=alice["headers"],
            json={"author_name": "Z", "work_title": "W"},
        )
        assert r2.status_code == 201
        # call_count 应该没增加(锁定后 analyze_compass_for_project 早返)
        assert call_count[0] == first_call_count
        # final_compass 仍是用户锁定的版本
        assert r2.json()["final_compass"] == {"merged": "user-final"}
        assert r2.json()["user_locked"] is True


# ============================================================
# Case 5 — PUT 锁定时没 final_compass → 422
# ============================================================

def test_put_lock_without_final_compass_returns_422(client, make_user):
    alice = make_user("alice")
    proj_id = _create_project(client, alice["headers"])

    # 先创建一条 compass(LLM mock 失败也行,只要记录在)
    with patch(
        "app.services.author_compass_service.call_llm_json",
        side_effect=RuntimeError("mock fail"),
    ), patch(
        "app.services.author_compass_service._get_full_text_for_project",
        return_value="测试" * 500,
    ):
        client.post(
            f"/api/projects/{proj_id}/author-compass/analyze",
            headers=alice["headers"],
            json={"author_name": "X"},
        )

    # 现在 final_compass 还是 NULL,尝试锁定
    r = client.put(
        f"/api/projects/{proj_id}/author-compass",
        headers=alice["headers"],
        json={"user_locked": True},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "FINAL_COMPASS_REQUIRED_BEFORE_LOCK"


# ============================================================
# Case 6 — 跨用户隔离
# ============================================================

def test_cross_user_isolation_returns_404(client, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    proj_id = _create_project(client, alice["headers"])

    # bob 访问 alice 的 compass 端点 → 404(因为项目对 bob 不存在)
    r = client.get(
        f"/api/projects/{proj_id}/author-compass",
        headers=bob["headers"],
    )
    assert r.status_code == 404

    r2 = client.post(
        f"/api/projects/{proj_id}/author-compass/analyze",
        headers=bob["headers"],
        json={"author_name": "X"},
    )
    assert r2.status_code == 404

    r3 = client.put(
        f"/api/projects/{proj_id}/author-compass",
        headers=bob["headers"],
        json={"author_name": "X"},
    )
    assert r3.status_code == 404
