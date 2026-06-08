"""阶段 7(2026-06-08)— 剧创态测试 fixture 套件。

从比赛仓库迁入的 234 个 test 大致分 3 类:
  1. **unit tests**(parsers / yaml_composer / yaml_validator / structure_analyzer /
     fidelity_scorer / cli / screenplay_exporter)— 直接调 service 函数,无 DB,
     **只需要改 import 路径就能跑**(父平台 conftest 提供了 DB / JWT 全套环境)
  2. **service tests**(ingest / story_bible / screenplay_store / compose)—
     有 DB 但不走 HTTP;需要 `user_id` 参数(阶段 3.5 SQL 隔离)
  3. **endpoint tests**(ingest_endpoint / compose_endpoint)— 走 HTTP TestClient,
     **必须带 Bearer JWT**(阶段 3 router-level Depends)+ 路径加 /api/screenplay 前缀

本 conftest 提供:
  - `screenplay_user` / `screenplay_user_token`:造一个父平台 user + 拿 JWT
  - `screenplay_client`:带 Bearer 的 TestClient
  - `another_user_token`:第二个 user(测跨用户隔离)
  - `mock_huimeng_bridge`:把 huimeng_bridge.get_*_block 全 stub 为空字符串
    (避免单元测试拿父平台 character_drivers_util 等的副作用)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection
from app.main import app
from app.models.user import User
from app.services.auth_service import issue_jwt


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_user(conn, *, email: str | None = None) -> str:
    """造一个 user 直接写 DB(绕过 OTP 流程)。返 user_id。"""
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, email or f"{uid[:8]}@screenplay-test.com", "free", _now(), _now()),
    )
    return uid


# ============================================================
# 比赛仓库 fixture 兼容层
# ============================================================
# 比赛仓库的测试用 `temp_db` / `client` 命名;父平台的 autouse `reset_test_db`
# 已经每测试重建 DB,这里两个 fixture 只是为了让迁入的测试代码无须改 fixture
# 命名就能跑。
#
# 关键:`temp_db` 不再 monkeypatch settings.database_path(父平台 settings 是
# 不可变 dataclass)— 而是依赖父平台 conftest 的 reset_test_db autouse 已经
# 把 TEST_DB 注入 HUIMENG_DB_PATH。这样所有 service / endpoint 自然走测试库。


@pytest.fixture
def temp_db():
    """比赛 fixture 兼容 — 父平台 reset_test_db autouse 已处理。直接 yield None。"""
    yield None


@pytest.fixture
def client(screenplay_client: TestClient) -> TestClient:
    """比赛 fixture 兼容 — 返带 Bearer JWT 的 TestClient(用 screenplay_user)。"""
    return screenplay_client


# ============================================================
# user_id 自动注入(autouse — 比赛测试无须改源码)
# ============================================================
# 比赛仓库的测试代码大量调 `ingest_service.persist_novel(parsed, "name.txt")`
# 这种省略 user_id 的形式 — 阶段 3.5 之后所有 service 函数都把 user_id 改成
# 必填了。手改 70+ 测试调用点工程量大,改用 monkeypatch shim:
#
#   1. autouse fixture 每个测试创建一个默认 user(via screenplay_user)
#   2. 把 ingest_service / story_bible_service / screenplay_store /
#      scene_splitter / compose_service 的所有 user_id 参数改为「未传时
#      用默认用户」
#
# 这是测试便利层 — 生产代码不动,只在测试运行时 patch。


@pytest.fixture(autouse=True)
def _inject_default_user_id_into_screenplay_services(request, monkeypatch):
    """autouse:把剧创态服务的 user_id 参数默认为「当前测试 user」。

    只对 tests/screenplay/ 下的测试生效(autouse 但条件触发)。
    """
    # 只在剧创测试触发(其他父平台测试不受影响)
    test_path = str(request.node.fspath)
    if "tests/screenplay" not in test_path.replace("\\", "/"):
        return

    # 造一个 user(每个测试一个,reset_test_db 已清表)
    conn = get_connection()
    try:
        uid = _make_user(conn, email=f"auto-{uuid.uuid4().hex[:6]}@test.com")
        conn.commit()
    finally:
        conn.close()

    # 把所有需要 user_id 的 service 函数 wrap 一层,默认值改为 uid
    from app.screenplay.services import (
        compose_service, ingest_service, screenplay_store, story_bible_service,
    )
    from app.screenplay.services.pipeline import scene_splitter

    def _wrap(fn, kw_name="user_id"):
        """返一个 wrapper,kw_name 缺省 = uid。"""
        def wrapper(*args, **kwargs):
            if kw_name not in kwargs:
                kwargs[kw_name] = uid
            return fn(*args, **kwargs)
        wrapper.__wrapped__ = fn  # type: ignore
        return wrapper

    # ingest_service
    for name in (
        "persist_novel", "delete_novel", "list_novels",
        "get_novel", "get_chapter_paragraphs", "is_novel_owned_by_user",
    ):
        orig = getattr(ingest_service, name, None)
        if orig is not None:
            monkeypatch.setattr(ingest_service, name, _wrap(orig))

    # story_bible_service
    for name in ("import_bible_from_json", "extract_bible_with_llm", "get_bible"):
        orig = getattr(story_bible_service, name, None)
        if orig is not None:
            monkeypatch.setattr(story_bible_service, name, _wrap(orig))

    # screenplay_store
    for name in (
        "save_screenplay", "get_latest_screenplay", "get_screenplay_by_id",
        "list_versions_for_novel", "list_screenplays",
    ):
        orig = getattr(screenplay_store, name, None)
        if orig is not None:
            monkeypatch.setattr(screenplay_store, name, _wrap(orig))

    # compose_service
    orig_orchestrate = getattr(compose_service, "orchestrate_full_pipeline", None)
    if orig_orchestrate is not None:
        monkeypatch.setattr(
            compose_service, "orchestrate_full_pipeline", _wrap(orig_orchestrate),
        )

    # scene_splitter pipeline
    orig_split_db = getattr(scene_splitter, "split_chapter_from_db", None)
    if orig_split_db is not None:
        monkeypatch.setattr(
            scene_splitter, "split_chapter_from_db", _wrap(orig_split_db),
        )

    # 记下 uid,让测试需要时通过 request.node.test_user_id 拿到
    request.node.test_user_id = uid


# ============================================================
# 用户 + JWT fixture
# ============================================================


@pytest.fixture
def screenplay_user() -> str:
    """造一个用户 + 返 user_id。"""
    conn = get_connection()
    try:
        uid = _make_user(conn)
        conn.commit()
    finally:
        conn.close()
    return uid


@pytest.fixture
def screenplay_user_token(screenplay_user: str) -> str:
    """用 screenplay_user 签一个 JWT,供 Bearer 用。"""
    user = User(
        id=screenplay_user, phone=None, email=None, plan="free",
        quota_reset_at=None, register_ip=None, register_ua=None,
        created_at=_now(), updated_at=_now(),
    )
    token, _ = issue_jwt(user)
    return token


@pytest.fixture
def screenplay_client(screenplay_user_token: str) -> Generator[TestClient, None, None]:
    """带 Bearer JWT 的 TestClient,所有调用自动带 Authorization header。"""
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {screenplay_user_token}"})
    yield client


@pytest.fixture
def another_user_token() -> str:
    """第二个 user — 测跨用户隔离用。"""
    conn = get_connection()
    try:
        uid = _make_user(conn, email="other-user@screenplay-test.com")
        conn.commit()
    finally:
        conn.close()
    user = User(
        id=uid, phone=None, email=None, plan="free",
        quota_reset_at=None, register_ip=None, register_ua=None,
        created_at=_now(), updated_at=_now(),
    )
    token, _ = issue_jwt(user)
    return token


# ============================================================
# 桥接层 mock — 让 service / endpoint 测试不依赖父平台 SP-2/3/4/7 数据
# ============================================================


@pytest.fixture
def mock_huimeng_bridge(monkeypatch):
    """把 huimeng_bridge.get_*_block 全 stub 为空字符串 / 空列表。

    用法:大量比赛迁入的 service 测试调 compose_service / scene_splitter,这些
    会通过 bridge 拉父平台资产。测试库没有那些数据,bridge 内部会 fallback 返
    空,但 fallback 路径需要 SQL JOIN 才知道 — 直接 stub 更快 + 隔离副作用。

    返一个 controller,可以用 `set_drivers(...)` 等方法注入具体响应。
    """

    class BridgeController:
        def __init__(self):
            self.drivers_block = ""
            self.knowledge_block = ""
            self.snapshots_block = ""
            self.polarity_block = ""
            self.facts_block = ""
            self.linked_characters: list[dict] = []
            self.calls: list[tuple[str, dict]] = []

        def set_drivers(self, block: str) -> None:
            self.drivers_block = block

        def set_knowledge(self, block: str) -> None:
            self.knowledge_block = block

        def set_polarity(self, block: str) -> None:
            self.polarity_block = block

        def set_facts(self, block: str) -> None:
            self.facts_block = block

        def set_snapshots(self, block: str) -> None:
            self.snapshots_block = block

        def set_linked_characters(self, items: list[dict]) -> None:
            self.linked_characters = items

    ctrl = BridgeController()

    def fake_drivers(conn, *, user_id, novel_id, character_names):
        ctrl.calls.append(("drivers", {
            "user_id": user_id, "novel_id": novel_id,
            "character_names": list(character_names or []),
        }))
        return ctrl.drivers_block

    def fake_knowledge(conn, *, user_id, novel_id, character_names,
                       current_scene_index=None):
        ctrl.calls.append(("knowledge", {
            "user_id": user_id, "novel_id": novel_id,
            "character_names": list(character_names or []),
            "current_scene_index": current_scene_index,
        }))
        return ctrl.knowledge_block

    def fake_polarity(conn, *, user_id, novel_id, character_names):
        ctrl.calls.append(("polarity", {
            "user_id": user_id, "novel_id": novel_id,
            "character_names": list(character_names or []),
        }))
        return ctrl.polarity_block

    def fake_facts(conn, *, user_id, novel_id, limit=20):
        ctrl.calls.append(("facts", {
            "user_id": user_id, "novel_id": novel_id, "limit": limit,
        }))
        return ctrl.facts_block

    def fake_snapshots(conn, *, user_id, novel_id, character_names):
        ctrl.calls.append(("snapshots", {
            "user_id": user_id, "novel_id": novel_id,
            "character_names": list(character_names or []),
        }))
        return ctrl.snapshots_block

    def fake_linked_characters(conn, *, user_id, novel_id):
        ctrl.calls.append(("linked_characters", {
            "user_id": user_id, "novel_id": novel_id,
        }))
        return ctrl.linked_characters

    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_character_drivers_block",
        fake_drivers,
    )
    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_character_knowledge_block",
        fake_knowledge,
    )
    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_relationship_polarity_block",
        fake_polarity,
    )
    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_story_facts_block",
        fake_facts,
    )
    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_character_snapshots_block",
        fake_snapshots,
    )
    monkeypatch.setattr(
        "app.screenplay.services.huimeng_bridge.get_linked_characters",
        fake_linked_characters,
    )
    return ctrl


# ============================================================
# LLM mock — 比赛仓库测试大量调 LLM,迁入后必须全程 mock
# ============================================================


@pytest.fixture
def mock_screenplay_llm(monkeypatch):
    """把剧创 LLM 调用全部 stub。

    比赛仓库的 8 个 agent(scene_splitter / element_extractor /
    dialogue_attributor / adaptation_decision / fidelity_scorer /
    structure_analyzer / screenplay_optimizer / story_bible_service)
    共用同一个 `app.screenplay.services.llm_client.call_json`,这里只
    monkeypatch 这一个函数。

    返 controller — `set_response(...)` 注入下一次返回。
    """

    class LlmController:
        def __init__(self):
            self.responses: list = []
            self.usage = {"input_tokens": 100, "output_tokens": 50}
            self.calls: list = []
            self.exception: Exception | None = None

        def set_response(self, data) -> None:
            """单次响应。下次调 LLM 返这个,然后清空。"""
            self.responses = [data]

        def queue_responses(self, *items) -> None:
            """多次响应,按 FIFO 消耗。"""
            self.responses = list(items)

        def raise_on_call(self, exc: Exception) -> None:
            self.exception = exc

    ctrl = LlmController()

    def fake_call(system_prompt, user_input, **kwargs):
        ctrl.calls.append({
            "system_len": len(system_prompt) if isinstance(system_prompt, str) else 0,
            "user": user_input,
        })
        if ctrl.exception is not None:
            raise ctrl.exception
        if not ctrl.responses:
            # 默认空响应 — 让测试自己挂 set_response 之前不要调 LLM
            return {}, ctrl.usage
        resp = ctrl.responses.pop(0)
        return resp, ctrl.usage

    monkeypatch.setattr(
        "app.screenplay.services.llm_client.call_json", fake_call,
    )
    return ctrl
