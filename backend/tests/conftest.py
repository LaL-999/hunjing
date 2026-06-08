"""pytest 全局 fixture。

关键设计:
- **必须在 import app 之前** 设置 HUIMENG_DB_PATH / HUIMENG_JWT_SECRET 环境变量
  (settings 在 import 时即加载,后改环境变量没用)
- 测试用独立 SQLite 文件(系统 temp 目录),每个测试前重建
- send_email_otp 全局 monkey patch,测试拿 patched_smtp fixture 即可拿到 sent codes
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# === 必须在 import app 之前设置环境变量 ===
TEST_DB = Path(tempfile.gettempdir()) / "huimeng_test.db"
os.environ["HUIMENG_DB_PATH"] = str(TEST_DB)
os.environ["HUIMENG_JWT_SECRET"] = "test_secret_at_least_48_bytes_xxxxxxxxxxxxxxxxxxxxxxxx"
os.environ["HUIMENG_JWT_TTL_SECONDS"] = "3600"

# 让测试能 import app(从 backend/ 目录启动 pytest 时)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# === 2026-06-02:session-start 全量编译预检 ===
# 治"经验值 25 — 中文字符串嵌套 ASCII 双引号"反复踩坑.
# 任何 service 模块 SyntaxError → 立刻 fail 所有测试,而不是等某个 fixture
# import 链触发时才暴露(可能漏在没跑到的测试文件里).
def _precompile_app_or_die() -> None:
    import compileall
    import io
    captured = io.StringIO()
    # quiet=1 = 只输出错误;rx 限制只看 app/ 下
    ok = compileall.compile_dir(
        str(BACKEND_ROOT / "app"),
        quiet=1,
        force=True,
        stripdir=str(BACKEND_ROOT),
    )
    if not ok:
        raise RuntimeError(
            "session-start 预编译失败 — app/ 下有 Python SyntaxError.\n"
            "常见原因:中文字符串内嵌套了 ASCII 双引号(经验值 25).\n"
            "解决:把内层 ASCII 双引号换成中文方括号「」或单引号."
        )

_precompile_app_or_die()

import pytest
from fastapi.testclient import TestClient

from app.db import _connect, transaction
from app.main import app


def _apply_migrations(db_path: Path) -> None:
    """跑 backend/migrations/ 下所有 SQL DDL 到 db_path.

    2026-06-02:与 app/main.py 的 auto-migration 对齐 — 兼容 ADD/DROP COLUMN 非幂等错误
    (重跑 + 新 DB 没有老列时,DROP COLUMN 会失败但应跳过).
    """
    import sqlite3 as _sq
    migrations_dir = BACKEND_ROOT / "migrations"
    conn = _connect(db_path)
    try:
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            sql = sql_file.read_text(encoding="utf-8")
            try:
                with transaction(conn) as tx:
                    tx.executescript(sql)
            except _sq.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column name" in msg:
                    continue  # ADD COLUMN 已应用,跳过
                if "no such column" in msg:
                    continue  # DROP COLUMN 列不存在(新 DB),跳过
                raise
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def reset_test_db():
    """每个测试前删除并重建 test.db。"""
    if TEST_DB.exists():
        TEST_DB.unlink()
    # WAL 模式遗留文件清理
    for ext in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(TEST_DB) + ext)
        if sidecar.exists():
            sidecar.unlink()

    _apply_migrations(TEST_DB)

    yield

    if TEST_DB.exists():
        TEST_DB.unlink()
    for ext in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(TEST_DB) + ext)
        if sidecar.exists():
            sidecar.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def patched_smtp(monkeypatch):
    """拦截 send_email_otp,记录所有调用 [(target_email, code), ...]。"""
    sent: list[tuple[str, str]] = []

    def fake_send(target_email: str, code: str) -> None:
        sent.append((target_email, code))

    # patch auth_service 引用的 send_email_otp(注意是 auth_service 模块里的引用)
    monkeypatch.setattr(
        "app.services.auth_service.send_email_otp", fake_send
    )
    return sent


@pytest.fixture
def patched_llm(monkeypatch):
    """注入 LLM 输出。用法:

        patched_llm.set_output([{...}, {...}])
        # 然后调用 refine API,LLM 调用会返回 set_output 的内容

    返回 controller 对象,可设置输出和读 calls。
    """
    class LlmController:
        def __init__(self):
            self.output: list = []
            self.usage = {"input_tokens": 1500, "output_tokens": 500}
            self.calls: list = []
            self.exception: Exception | None = None

        def set_output(self, data: list) -> None:
            self.output = data

        def set_usage(self, input_tokens: int, output_tokens: int) -> None:
            self.usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}

        def raise_on_call(self, exc: Exception) -> None:
            self.exception = exc

    ctrl = LlmController()

    def fake_call(system_prompt, user_input, **kwargs):
        ctrl.calls.append({"system_len": len(system_prompt), "user": user_input})
        if ctrl.exception is not None:
            raise ctrl.exception
        return ctrl.output, ctrl.usage

    monkeypatch.setattr("app.services.refine_service.call_llm_json", fake_call)
    return ctrl


@pytest.fixture
def patched_simulation_llm(monkeypatch):
    """注入 simulation_service 的 LLM 调用 — Sprint 1.G。

    用法:
        patched_simulation_llm.json_queue.extend([dir_plan, agent_out, ...])
        patched_simulation_llm.text_queue.append(narrative_md)

    队列按 FIFO 顺序消耗;空队列再被调用 → 抛 AssertionError 提示 preset 不够。
    队列项是 Exception 实例时,该次调用直接 raise(模拟 LLM 失败)。
    """

    class SimLlmController:
        def __init__(self) -> None:
            self.json_queue: list = []
            self.text_queue: list = []
            self.json_calls: list[dict] = []
            self.text_calls: list[dict] = []
            self.usage_per_call = {"input_tokens": 100, "output_tokens": 50}

    ctrl = SimLlmController()

    def fake_json(system_prompt, user_input, **kwargs):
        # 3.A polish:同步记录 user_prompt(末尾态测试需要断言 prompt 含【原作末段】)
        # 老 system_len 字段保留,向下兼容老断言
        ctrl.json_calls.append({
            "system_len": len(system_prompt),
            "user_prompt": user_input,
        })
        if not ctrl.json_queue:
            raise AssertionError(
                "patched_simulation_llm.json_queue 已空但仍被调用 — 测试 preset 不够"
            )
        out = ctrl.json_queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, ctrl.usage_per_call

    def fake_text(system_prompt, user_input, **kwargs):
        ctrl.text_calls.append({
            "system_len": len(system_prompt),
            "user_prompt": user_input,
        })
        if not ctrl.text_queue:
            raise AssertionError(
                "patched_simulation_llm.text_queue 已空但仍被调用 — 测试 preset 不够"
            )
        out = ctrl.text_queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, ctrl.usage_per_call

    monkeypatch.setattr("app.services.simulation_service.call_llm_json", fake_json)
    monkeypatch.setattr("app.services.simulation_service.call_llm_text", fake_text)
    return ctrl


@pytest.fixture
def sync_simulation_runner(monkeypatch):
    """把 simulation_service.kick_off 换成同步直跑 — Sprint 1.G 测试支撑。

    生产环境 kick_off 用 asyncio task 后台跑,POST 立即返回 'queued';
    测试中我们要在 POST 响应体上断言最终状态(done/failed),所以同步阻塞跑。
    """
    import app.services.simulation_service as svc

    monkeypatch.setattr(svc, "kick_off", svc.run_simulation)


@pytest.fixture
def make_user(client: "TestClient", patched_smtp):
    """工厂 fixture:走 OTP 流程创建并登录一个用户。

    用法:
        u = make_user("alice")
        # u = {"token": "...", "user_id": "...", "email": "alice1@example.com"}

    内部按 prefix + 自增编号确保邮箱唯一,避免同邮箱 60s 限频。
    """
    counter = [0]

    def _make(email_prefix: str = "user") -> dict:
        counter[0] += 1
        email = f"{email_prefix}{counter[0]}@example.com"
        send_resp = client.post("/api/auth/send_otp", json={"email": email})
        assert send_resp.status_code == 200, f"send_otp failed: {send_resp.text}"
        code = patched_smtp[-1][1]
        verify_resp = client.post(
            "/api/auth/verify", json={"email": email, "code": code}
        )
        assert verify_resp.status_code == 200, f"verify failed: {verify_resp.text}"
        body = verify_resp.json()
        return {
            "token": body["token"],
            "user_id": body["user_id"],
            "email": email,
            "headers": {"Authorization": f"Bearer {body['token']}"},
        }

    return _make
