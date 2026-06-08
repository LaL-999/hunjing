"""
BYOK 自携密钥 — 调用上下文(ContextVar 封装)。

设计原因:
  - LLM 调用链最深处(llm_client.call_llm_*)需要知道"当前是哪个用户在调",
    但已有 33 个调用点不传 user_id,改签名工作量大且易错
  - 用 contextvars 在 endpoint 入口设置一次,LLM client 在最深处读出即可
  - 同步函数链(99% 调用)零修改,Python contextvars 自动跟随

后台线程注意事项:
  - asyncio.create_task 自动 copy context(标准库行为)
  - threading.Thread 默认**不** copy context → 必须显式 contextvars.copy_context().run(...)
  - simulation_service / outline async runner 这 2 处需要改

使用:
  # endpoint 层(Depends 注入时自动 set)
  set_current_user_id(user.id)

  # service / llm client 任何位置
  uid = get_current_user_id()  # None 表示无登录态

  # 后台线程启动前
  ctx = capture_current_context()
  threading.Thread(target=ctx.run, args=(work,)).start()
"""
from __future__ import annotations

import contextvars
from typing import Optional

# ContextVar 全局单例 — module-level,跨 import 同一个
_current_user_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "byok_current_user_id",
    default=None,
)


def set_current_user_id(user_id: Optional[str]) -> None:
    """设置当前调用栈的用户 ID(从 endpoint Depends 注入时调用)。

    设 None = 清除(登出 / 游客)。
    """
    _current_user_id_ctx.set(user_id)


def get_current_user_id() -> Optional[str]:
    """读取当前调用栈用户 ID。

    返 None = 未登录 / 上下文未设置 / 后台 thread 没继承 context。
    LLM client 调用此函数,None 时走平台默认 key。
    """
    return _current_user_id_ctx.get()


def capture_current_context() -> contextvars.Context:
    """捕获当前完整 context,用于 threading.Thread.target=ctx.run。

    用法:
        ctx = capture_current_context()
        threading.Thread(target=ctx.run, args=(work_func, arg1, arg2)).start()

    或更通用的:
        ctx = capture_current_context()
        threading.Thread(target=lambda: ctx.run(work_func, ...)).start()
    """
    return contextvars.copy_context()
