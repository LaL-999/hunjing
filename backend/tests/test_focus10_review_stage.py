"""Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核阶段
  - 抽完 graph → state=entities_pending_review,worker 暂停
  - 用户 POST /approve_entities → 改 extracted_graph_json + state=generating_characters
  - kick_off_extract 续跑(fast-path 跳过 graph 阶段)→ 进 profile → done

测试用 sync_extract_runner 同步跑,但**自己控制 approve 时机**(不让 fixture 自动 approve)。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def sync_no_auto_approve(monkeypatch):
    """同步跑 run_extract,但**不自动 approve** — 测试要验证暂停在 review 状态。"""
    import app.services.extract_service as svc
    monkeypatch.setattr(svc, "kick_off_extract", svc.run_extract)


@pytest.fixture
def patched_extract_funcs(monkeypatch):
    """从 test_extract.py 复用的 LLM 函数 mock fixture。"""
    class Ctrl:
        graph_output: dict = {"entities": [], "relations": []}
        profile_outputs: dict = {}
        meta_output: dict = {"type": "novel", "custom_type_name": None, "tags": []}
        usage: dict = {"input_tokens": 100, "output_tokens": 50}
        minimal_batch_output: dict = {}

    ctrl = Ctrl()

    def fake_extract_graph_chunked(
        text, work_name="", work_type="作品", chunk_chars=25000,
        on_chunk_done=None, on_chunk_failed=None,
        on_chunk_skipped=None, completed_chunks=None, should_cancel=None,
    ):
        completed_chunks = completed_chunks or {}
        total = max(1, 1 + len(completed_chunks))
        if on_chunk_done is not None:
            on_chunk_done(
                len(completed_chunks) + 1, total, ctrl.usage,
                "fakehash_1", ctrl.graph_output,
            )
        return ctrl.graph_output, ctrl.usage

    def fake_generate_character_profile(work_name, language_style, char_entity, source_text):
        name = char_entity.get("name")
        return ctrl.profile_outputs.get(name, {}), ctrl.usage

    def fake_generate_minimal_profiles_batch(
        work_name, language_style, person_entities, source_text,
        excerpts_per_person_chars=1000,
    ):
        out = []
        for ent in person_entities:
            name = ent.get("name", "")
            out.append({
                "name": name,
                "personality": f"{name} 性格",
                "quotes": [f"{name} 台词"],
                "no_go_list": [],
            })
        return out, ctrl.usage

    def fake_infer_meta(work_name, full_text):
        return ctrl.meta_output, ctrl.usage

    monkeypatch.setattr(
        "app.services.extract_service.extract_graph_chunked", fake_extract_graph_chunked,
    )
    monkeypatch.setattr(
        "app.services.extract_service.generate_character_profile",
        fake_generate_character_profile,
    )
    monkeypatch.setattr(
        "app.services.extract_service.generate_minimal_profiles_batch",
        fake_generate_minimal_profiles_batch,
    )
    monkeypatch.setattr(
        "app.services.extract_service.infer_meta", fake_infer_meta,
    )
    return ctrl


def _create_middle_project_with_upload(client, headers):
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    project_id = p["id"]
    files = {"file": ("test.txt", b"\xe6\xb5\x8b\xe8\xaf\x95\xe6\x96\x87\xe6\x9c\xac" * 100, "text/plain")}
    upload = client.post(
        f"/api/projects/{project_id}/uploads", headers=headers, files=files,
    ).json()
    return project_id, upload["id"]


def _setup_outputs(patched):
    patched.graph_output = {
        "entities": [
            {"name": "甲", "type": "PERSON", "aliases": [], "description": "主角"},
            {"name": "乙", "type": "PERSON", "aliases": [], "description": "配角"},
            {"name": "丙", "type": "PERSON", "aliases": [], "description": "可能漏抽的角色"},
        ],
        "relations": [
            {"source": "甲", "target": "乙", "type": "朋友", "strength": "strong", "description": ""},
        ],
        "meta": {"narrative_pov": "third"},
    }
    patched.profile_outputs = {
        "甲": {"identity": "i1", "personality": "p1",
              "voice_fingerprint": {"quotes": ["q1"]}, "no_go_list": []},
        "乙": {"identity": "i2", "personality": "p2",
              "voice_fingerprint": {"quotes": ["q2"]}, "no_go_list": []},
        "丙": {"identity": "i3", "personality": "p3",
              "voice_fingerprint": {"quotes": ["q3"]}, "no_go_list": []},
    }
    patched.minimal_batch_output = {}
    patched.meta_output = {"type": "novel", "tags": ["测试"]}
    patched.usage = {"input_tokens": 100, "output_tokens": 50}


def test_extract_pauses_in_review_state(
    client: TestClient, make_user, sync_no_auto_approve, patched_extract_funcs,
):
    """trigger 后,worker 跑完 graph 阶段就暂停在 entities_pending_review。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _setup_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    # **关键断言**:停在 entities_pending_review,**没**进入 done
    assert body["state"] == "entities_pending_review"


def test_approve_entities_with_no_changes_advances_to_done(
    client: TestClient, make_user, sync_no_auto_approve, patched_extract_funcs,
):
    """空 approve(添加 / 删除都空)= "全部接受",流程推进到 done。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _setup_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    r2 = client.post(
        f"/api/extract_jobs/{job_id}/approve_entities", headers=h,
        json={"added_persons": [], "removed_names": []},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["state"] == "done"
    # 3 个 PERSON 全部入库
    assert body["characters_count"] == 3


def test_approve_with_removed_names_filters_persons_and_relations(
    client: TestClient, make_user, sync_no_auto_approve, patched_extract_funcs,
):
    """approve 时 removed_names=[丙] → DB 里只有甲乙;指向丙的 relations 也清。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _setup_outputs(patched_extract_funcs)
    # 加一条指向丙的关系
    patched_extract_funcs.graph_output["relations"].append(
        {"source": "甲", "target": "丙", "type": "敌对", "strength": "strong", "description": ""}
    )

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    r2 = client.post(
        f"/api/extract_jobs/{job_id}/approve_entities", headers=h,
        json={"added_persons": [], "removed_names": ["丙"]},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["state"] == "done"
    assert body["characters_count"] == 2   # 丙被过滤

    # 看真实角色名
    chars = client.get(f"/api/projects/{project_id}/characters", headers=h).json()
    names = {c["name"] for c in chars}
    assert "甲" in names and "乙" in names
    assert "丙" not in names

    # 关系清:指向"丙"的边消失
    rels = client.get(f"/api/projects/{project_id}/relationships", headers=h).json()
    target_chars = {
        next((c["name"] for c in chars if c["id"] == r["target_id"]), "?")
        for r in rels
    }
    assert "丙" not in target_chars


def test_approve_with_added_persons_inserts_new_characters(
    client: TestClient, make_user, sync_no_auto_approve, patched_extract_funcs,
):
    """approve 时 added_persons=[{name:丁, description:...}] → DB 里有 4 个角色。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _setup_outputs(patched_extract_funcs)
    # 丁也要有 profile output(profile 阶段会为新角色生成档案)
    patched_extract_funcs.profile_outputs["丁"] = {
        "identity": "i4", "personality": "p4",
        "voice_fingerprint": {"quotes": ["q4"]}, "no_go_list": [],
    }

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    r2 = client.post(
        f"/api/extract_jobs/{job_id}/approve_entities", headers=h,
        json={
            "added_persons": [{"name": "丁", "description": "用户手动 + 的漏抽角色"}],
            "removed_names": [],
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["state"] == "done"
    assert body["characters_count"] == 4   # 原 3 + 用户 + 1

    chars = client.get(f"/api/projects/{project_id}/characters", headers=h).json()
    names = {c["name"] for c in chars}
    assert "丁" in names


def test_approve_rejects_when_not_in_review_state(
    client: TestClient, make_user, sync_no_auto_approve, patched_extract_funcs,
):
    """job 状态非 entities_pending_review 时,approve 返回 409。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, upload_id = _create_middle_project_with_upload(client, h)
    _setup_outputs(patched_extract_funcs)

    r = client.post(f"/api/uploads/{upload_id}/extract", headers=h)
    job_id = r.json()["id"]

    # 先 approve 一次(进入 done)
    r2 = client.post(
        f"/api/extract_jobs/{job_id}/approve_entities", headers=h,
        json={"added_persons": [], "removed_names": []},
    )
    assert r2.json()["state"] == "done"

    # 再 approve 一次 → 409(state=done 不在 review)
    r3 = client.post(
        f"/api/extract_jobs/{job_id}/approve_entities", headers=h,
        json={"added_persons": [], "removed_names": []},
    )
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "EXTRACT_NOT_IN_REVIEW"
