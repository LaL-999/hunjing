"""INS-A5/A6 二级数据 agent 测试(2026-05-27).

验证:
  1. LLM mock 模式可用(set_mock_response)
  2. Level 1 触发条件 C — < 50 事件跳过
  3. Level 1 ≥ 50 事件 → 调 LLM 落 daily_report
  4. Level 1 幂等 — 同日重跑会覆盖
  5. Level 2 触发条件 — 累积日报 < 30 不跑
  6. Level 2 force 模式 → 跑出阶段汇总
  7. Level 2 把已覆盖的 dailies 排除掉(下次只看新增)
  8. /agent/status 返回完整触发状态
  9. /agent/run-daily 单日 / 批量模式
 10. /agent/summaries 列出 + 单详情
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """每个 test 独立 analytics.db + 启用 LLM mock."""
    monkeypatch.setenv("INSIGHTS_ANALYTICS_DB", str(tmp_path / "analytics.db"))
    monkeypatch.setenv("INSIGHTS_HUIMENG_DB", str(tmp_path / "huimeng_not_exist.db"))
    monkeypatch.setenv("INSIGHTS_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("INSIGHTS_LLM_MOCK_MODE", "1")

    import importlib
    import app.config
    importlib.reload(app.config)
    import app.db
    importlib.reload(app.db)
    import app.services.llm_client
    importlib.reload(app.services.llm_client)
    import app.services.level1_agent
    importlib.reload(app.services.level1_agent)
    import app.services.level2_agent
    importlib.reload(app.services.level2_agent)
    import app.routers.track
    importlib.reload(app.routers.track)
    import app.routers.admin
    importlib.reload(app.routers.admin)
    import app.routers.agent
    importlib.reload(app.routers.agent)
    import app.main
    importlib.reload(app.main)


@pytest.fixture
def client(isolate_db):
    import app.main
    with TestClient(app.main.app) as c:
        yield c


def _post_events(client, count: int, target_day_offset_days: int = 0):
    """给某一天插 N 个 events(默认今天)."""
    day = date.today() - timedelta(days=target_day_offset_days)
    base_ms = int(time.mktime(day.timetuple()) * 1000) + 12 * 3600 * 1000  # 中午
    for i in range(count):
        client.post("/track", json={
            "event_type": "page_view" if i % 3 != 0 else "ai_call_done",
            "user_id": f"u-{i % 5}",
            "session_id": f"s-{i % 10}",
            "timestamp_ms": base_ms + i,
            "path": f"/projects/p-{i % 3}",
            "mode": ["initial", "middle", "tail"][i % 3],
            "duration_ms": 1000 * (i % 60),
            "meta": {"ai_name": "narrator"} if i % 3 == 0 else {},
        })


# ============================================================
# 1. LLM mock 模式
# ============================================================

def test_llm_mock_mode_returns_injected_response():
    from app.services.llm_client import call_llm_json, set_mock_response
    set_mock_response({"hello": "world"})
    parsed, usage = call_llm_json("sys", "user")
    assert parsed == {"hello": "world"}
    assert usage["input_tokens"] > 0
    set_mock_response(None)


def test_llm_mock_mode_no_response_returns_stub():
    from app.services.llm_client import call_llm_json, set_mock_response
    set_mock_response(None)
    parsed, usage = call_llm_json("sys", "user")
    assert parsed == {"_mocked": True}


# ============================================================
# 2-4. Level 1
# ============================================================

def test_level1_skips_when_events_below_threshold(client):
    """< 50 事件 → 跳过 + 落 skipped 记录."""
    _post_events(client, 20, target_day_offset_days=1)   # 昨天 20 个事件
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    r = client.post(
        "/agent/run-daily",
        json={"target_date": yesterday},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["skipped"] is True
    assert "事件不足" in body["result"]["skip_reason"]
    assert body["result"]["report_id"] is not None
    assert body["result"]["cost_yuan"] == 0.0


def test_level1_runs_llm_when_events_above_threshold(client):
    """≥ 50 事件 → 调 LLM(mock)落正常 daily_report."""
    from app.services.llm_client import set_mock_response
    set_mock_response({
        "summary_md": "今日活跃用户 5 人,AI 调用 17 次集中在 narrator",
        "metrics": {"highlight_count": 2, "anomaly_count": 0},
        "tags": ["活跃", "AI 高频"],
    })

    _post_events(client, 60, target_day_offset_days=1)   # 60 个事件
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    r = client.post(
        "/agent/run-daily",
        json={"target_date": yesterday},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["skipped"] is False
    assert body["result"]["report_id"] is not None
    assert body["result"]["event_count"] == 60
    set_mock_response(None)


def test_level1_idempotent_overwrite_same_date(client):
    """同日重跑 → 覆盖旧 report_id(新 id 不同)."""
    from app.services.llm_client import set_mock_response
    set_mock_response({"summary_md": "第一次", "metrics": {}, "tags": []})

    _post_events(client, 60, target_day_offset_days=1)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    r1 = client.post(
        "/agent/run-daily",
        json={"target_date": yesterday},
        headers={"X-Admin-Token": "test-token"},
    )
    id1 = r1.json()["result"]["report_id"]

    set_mock_response({"summary_md": "第二次重跑", "metrics": {}, "tags": []})
    r2 = client.post(
        "/agent/run-daily",
        json={"target_date": yesterday},
        headers={"X-Admin-Token": "test-token"},
    )
    id2 = r2.json()["result"]["report_id"]

    # id 应不同(旧记录被删,新记录插入)
    assert id1 != id2

    # /agent/daily 应只剩 1 份(同日)
    r = client.get(
        "/agent/daily?limit=10",
        headers={"X-Admin-Token": "test-token"},
    )
    dates = [rep["report_date"] for rep in r.json()["reports"]]
    assert dates.count(yesterday) == 1
    set_mock_response(None)


# ============================================================
# 5-7. Level 2
# ============================================================

def test_level2_skips_when_below_threshold(client):
    """累积日报 < 30 + 距上次汇总 < 30 天 → skipped."""
    r = client.post(
        "/agent/run-summary",
        json={},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] is True
    assert "未汇总" in body["skip_reason"] or "累积" in body["skip_reason"]


def test_level2_force_runs_summary(client):
    """force=True 跳过触发判定 → 跑 LLM."""
    from app.services.llm_client import set_mock_response
    set_mock_response({"summary_md": "(stub) 第 1 阶段汇总", "metrics": {}, "tags": []})

    # 先塞几份 daily_reports(有 summary_md 才会被纳入)
    _post_events(client, 60, target_day_offset_days=2)
    _post_events(client, 60, target_day_offset_days=1)
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    one_day_ago = (date.today() - timedelta(days=1)).isoformat()
    set_mock_response({"summary_md": "day 1", "metrics": {}, "tags": []})
    client.post("/agent/run-daily", json={"target_date": two_days_ago},
                headers={"X-Admin-Token": "test-token"})
    set_mock_response({"summary_md": "day 2", "metrics": {}, "tags": []})
    client.post("/agent/run-daily", json={"target_date": one_day_ago},
                headers={"X-Admin-Token": "test-token"})

    # force 模式跑 summary
    set_mock_response({
        "summary_md": "## 第 1 阶段汇总\n核心趋势:活跃稳步上升",
        "insights": {
            "trends": [{"name": "活跃", "direction": "up", "detail": "稳步增长"}],
            "anomalies": [],
            "patterns": [],
            "recommendations": [],
        },
        "tags": ["稳步", "上升"],
    })
    r = client.post(
        "/agent/run-summary",
        json={"force": True},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["skipped"] is False
    assert body["summary_id"] is not None
    assert body["stage_number"] == 1
    assert body["daily_count"] == 2
    set_mock_response(None)


def test_level2_excludes_already_summarized_dailies(client):
    """跑过 summary 后,下次 force 只看新 daily_reports."""
    from app.services.llm_client import set_mock_response

    # 第 1 批 daily_reports + summary
    _post_events(client, 60, target_day_offset_days=3)
    set_mock_response({"summary_md": "d1", "metrics": {}, "tags": []})
    client.post(
        "/agent/run-daily",
        json={"target_date": (date.today() - timedelta(days=3)).isoformat()},
        headers={"X-Admin-Token": "test-token"},
    )
    set_mock_response({
        "summary_md": "stage 1", "insights": {}, "tags": [],
    })
    r1 = client.post(
        "/agent/run-summary",
        json={"force": True},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r1.json()["daily_count"] == 1

    # 加新 daily_report
    _post_events(client, 60, target_day_offset_days=1)
    set_mock_response({"summary_md": "d3", "metrics": {}, "tags": []})
    client.post(
        "/agent/run-daily",
        json={"target_date": (date.today() - timedelta(days=1)).isoformat()},
        headers={"X-Admin-Token": "test-token"},
    )

    # 第 2 次 summary force — 只应包括新 1 份
    set_mock_response({
        "summary_md": "stage 2", "insights": {}, "tags": [],
    })
    r2 = client.post(
        "/agent/run-summary",
        json={"force": True},
        headers={"X-Admin-Token": "test-token"},
    )
    body = r2.json()
    assert body["stage_number"] == 2
    assert body["daily_count"] == 1   # 只包含新增的 1 份
    set_mock_response(None)


# ============================================================
# 8-10. /agent/* 端点
# ============================================================

def test_agent_status_no_data(client):
    r = client.get("/agent/status", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["level1"]["total_reports"] == 0
    assert body["level1"]["pending_count"] == 0
    assert body["level1"]["min_events_threshold"] == 50
    assert body["level2"]["total_summaries"] == 0
    assert body["level2"]["should_run"] is False


def test_run_daily_batch_mode(client):
    """target_date 不传 → 跑所有 pending 日期."""
    from app.services.llm_client import set_mock_response
    _post_events(client, 60, target_day_offset_days=1)
    _post_events(client, 30, target_day_offset_days=2)   # < 50,会 skip

    set_mock_response({"summary_md": "batch", "metrics": {}, "tags": []})
    r = client.post(
        "/agent/run-daily",
        json={},
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "batch"
    assert body["pending_count"] >= 1
    # 至少一份有效 + 一份 skipped
    skipped_count = sum(1 for r in body["results"] if r["skipped"])
    valid_count = sum(1 for r in body["results"] if not r["skipped"])
    assert skipped_count + valid_count == body["pending_count"]
    set_mock_response(None)


def test_summaries_list_and_detail(client):
    """造一份 summary 再查列表 + 详情."""
    from app.services.llm_client import set_mock_response
    _post_events(client, 60, target_day_offset_days=1)
    set_mock_response({"summary_md": "d", "metrics": {}, "tags": []})
    client.post(
        "/agent/run-daily",
        json={"target_date": (date.today() - timedelta(days=1)).isoformat()},
        headers={"X-Admin-Token": "test-token"},
    )
    set_mock_response({
        "summary_md": "**第 1 阶段** 全平台稳健",
        "insights": {"trends": [], "recommendations": []},
        "tags": ["稳健"],
    })
    r = client.post(
        "/agent/run-summary",
        json={"force": True},
        headers={"X-Admin-Token": "test-token"},
    )
    sid = r.json()["summary_id"]

    # 列表
    r = client.get("/agent/summaries", headers={"X-Admin-Token": "test-token"})
    body = r.json()
    assert len(body["summaries"]) == 1
    assert body["summaries"][0]["id"] == sid
    assert body["summaries"][0]["stage_number"] == 1

    # 详情
    r = client.get(f"/agent/summaries/{sid}", headers={"X-Admin-Token": "test-token"})
    body = r.json()
    assert "第 1 阶段" in body["summary_md"]
    assert "稳健" in body.get("insights", {}).get("tags", [])

    # mark-read
    r = client.post(
        f"/agent/summaries/{sid}/mark-read",
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.json()["ok"] is True

    # 再查详情应有 read_at
    r = client.get(f"/agent/summaries/{sid}", headers={"X-Admin-Token": "test-token"})
    assert r.json()["read_at"] is not None
    set_mock_response(None)


def test_summary_not_found(client):
    r = client.get("/agent/summaries/9999", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 404


def test_agent_endpoints_require_admin(client):
    """所有 /agent/* 端点要 admin token."""
    endpoints = [
        ("GET", "/agent/status"),
        ("POST", "/agent/run-daily"),
        ("POST", "/agent/run-summary"),
        ("GET", "/agent/daily"),
        ("GET", "/agent/summaries"),
    ]
    for method, ep in endpoints:
        if method == "GET":
            r = client.get(ep)
        else:
            r = client.post(ep, json={})
        assert r.status_code == 401, f"{method} {ep} 无 token 应 401"
