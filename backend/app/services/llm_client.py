"""LLM 客户端 — 公开 API 委托给 llm_routing 路由层(Sprint D.8 改造)。

Sprint D.8 之前:本文件直接 OpenAI SDK 调 DeepSeek,单 vendor 硬编码。
Sprint D.8 之后:
  - `call_llm_json` / `call_llm_text` / `estimate_cost_yuan` **签名保持不变**
  - 内部委托给 `llm_routing.router` — 实际 adapter 由 `settings.text_vendor` 决定
  - 默认 `text_vendor='deepseek'`,行为与 D.8 之前完全一致
  - DeepSeekTextAdapter 复用本文件下方 `_openai_compat_call_*` impl(OpenAI 兼容端点)
  - 其它 vendor(qwen / moonshot 等)未来加新 adapter 时也可复用同一 impl

向后兼容铁律:
  - 所有 service 层(refine / simulation / canonical_guardian / extract / de_ip / audit)
    调用 `call_llm_json(prompt, user)` / `call_llm_text(...)` / `estimate_cost_yuan(in, out)`
    都**不改一行**,通过本文件的 thin wrapper 自动走新路由
  - 异常类 `LlmCallFailed` / `LlmJsonParseFailed` 仍由本文件定义,llm_routing.protocols 重导出
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


class LlmCallFailed(Exception):
    """LLM 调用底层失败(网络 / 限流 / 凭据缺失)。"""


class LlmJsonParseFailed(Exception):
    """LLM 调用成功但响应不是合法 JSON。"""


# ============================================================
# CT-OPT.3(2026-05-21)— 429 限流退避重试
# ============================================================

def _is_rate_limit_error(e: Exception) -> bool:
    """判定异常是否为 LLM provider 限流(429 Too Many Requests)。

    支持 openai SDK 1.x 的异常体系(RateLimitError / APIError + status 429)
    + 通用 fallback:异常 str 含 "429" / "rate limit" / "rate_limit_exceeded"
    """
    # openai SDK 1.x 内置 RateLimitError
    try:
        from openai import RateLimitError  # type: ignore[attr-defined]
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass

    # 通用 fallback:看异常 message / repr
    msg = (str(e) or "").lower()
    if "429" in msg:
        return True
    if "rate limit" in msg or "rate_limit_exceeded" in msg:
        return True
    if "too many requests" in msg:
        return True

    # 部分 SDK 把 status_code 挂在 e.response.status_code
    status_code = None
    if hasattr(e, "status_code"):
        status_code = getattr(e, "status_code", None)
    elif hasattr(e, "response"):
        resp = getattr(e, "response", None)
        if resp is not None:
            status_code = getattr(resp, "status_code", None)
    if status_code == 429:
        return True

    return False


def _backoff_sleep_seconds(attempt: int) -> float:
    """限流退避时长 — 指数退避,1s → 2s → 4s,上限 8s。

    attempt 从 0 开始(第 1 次重试 attempt=0 → sleep 1s)。
    """
    return min(8.0, 1.0 * (2 ** attempt))


# ============================================================
# helpers — adapter / 外部代码都可复用
# ============================================================

def _strip_markdown_fence(s: str) -> str:
    """剥掉 ```json ... ``` 包裹(对齐 simulate.py)。"""
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON|markdown|md)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _extract_json_block(s: str) -> Optional[str]:
    """Sprint 5.x bug fix(2026-05-14):从含 markdown 注释 / 标题 / 多段 ``` 的混杂输出里
    挖出第一个完整 JSON 对象。

    用括号匹配从第一个 `{` 找到与之配对的 `}`,跳过字符串内的花括号(处理转义 `\\"`)。
    适合处理 LLM 不遵守"严格 JSON"规则、输出"# 阶段 1...\\n```json\\n{...}\\n```\\n# 阶段 2..."
    这种 markdown 报告 + 内嵌 JSON 的场景。

    返回:挖出的 JSON 子串(`{...}`),或 None(找不到平衡的对)。
    """
    if not s:
        return None
    # 找第一个 { 起点
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(s)):
        c = s[i]
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if c == "\\":
                escape_next = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None  # 不平衡


def _repair_llm_json(s: str) -> str:
    """温和修复 LLM 偶发输出的 invalid JSON(只动语法不改内容)。

    已知坑(Sprint 1.G + 1.O 长 context 推演踩过):
      - LLM 把 prompt 里的 `{{` `}}` 例子照搬输出(本来 .format() 应转义)
        → 整体首尾出现 `{{ ... }}`,标准 json 拒
      - LLM 在 JSON 末尾加 markdown 注释 `// 这是 ...`(部分 deepseek-chat 习惯)
      - LLM 把单引号对换错(` 中文引号 → 英文引号),JSON 里的纯中文不影响

    只改首尾的 `{{ ... }}`,不动中间 — 中间 {{ }} 可能是真内容(虽然罕见)。
    """
    s = s.strip()
    # 首尾双花括号 → 单花括号(对齐 .format 语义)
    if s.startswith("{{") and s.endswith("}}"):
        s = s[1:-1]
    elif s.startswith("{{"):
        s = s[1:]
    elif s.endswith("}}"):
        s = s[:-1]
    return s


# ============================================================
# Sprint D.8 — OpenAI 兼容端点 impl(被 adapter 复用)
# ============================================================
#
# 把原 call_llm_json / call_llm_text 的实现搬到这里,接受 api_key / api_base / model
# 作为参数(原本写死 settings 全局)。DeepSeek / Qwen / Moonshot 等任何走 OpenAI
# 兼容协议的 vendor 都能复用,只需 adapter 传不同 key + base + model。

def _openai_compat_call_json(
    system_prompt: str,
    user_input: dict | str,
    *,
    api_key: str,
    api_base: str,
    model: str,
    vendor_label: str,
    max_tokens: int = 4000,
    temperature: float = 0.6,
    retries: int = 2,
    timeout: float = 60.0,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
) -> tuple[Any, dict]:
    """OpenAI 兼容端点的 JSON 调用 — 给 DeepSeekTextAdapter / QwenTextAdapter 等复用。

    返回 (parsed_json, usage_dict)。失败抛 LlmCallFailed / LlmJsonParseFailed。

    frequency_penalty / presence_penalty(提案 B,2026-05-24):
      OpenAI 兼容协议的标准参数,治"n-gram 原文复读" — LLM 输出同一句话两次。
      DeepSeek 兼容,默认 0.0 = 不开启 = 与改造前行为完全一致。
      narrator / agent_dialogue 等长文本输出场景显式传 0.3~0.5 即可。
    """
    from openai import OpenAI

    if not api_key:
        raise LlmCallFailed(
            f"{vendor_label} API key 未配置(请在项目根 .env 填对应字段)"
        )
    # 防之前踩过的中文占位符坑
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as e:
        raise LlmCallFailed(
            f"{vendor_label} API key 含非 ASCII 字符(可能是占位符):{e}"
        ) from e

    client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
    user_content = (
        json.dumps(user_input, ensure_ascii=False, indent=2)
        if isinstance(user_input, dict)
        else user_input
    )

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
            )
            raw = resp.choices[0].message.content or ""
            usage = {
                "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            }
            # Sprint 5.x bug fix(2026-05-14):3 级 fallback 解析(原 2 级):
            #   ① _strip_markdown_fence:剥首尾 ```json ... ``` 包裹(标准情况)
            #   ② _extract_json_block:括号匹配从混杂 markdown 输出里挖 JSON
            #      (LLM 出"# 阶段 1...\n```json\n{...}\n```\n# 阶段 2..." 这种违规格式)
            #   ③ _repair_llm_json:首尾 `{{ }}` 双花括号修复(老坑)
            cleaned = _strip_markdown_fence(raw)
            parsed = None
            parse_errors: list[str] = []
            for stage_name, candidate in [
                ("strip_fence", cleaned),
                ("extract_block", _extract_json_block(cleaned) or _extract_json_block(raw)),
                ("repair", _repair_llm_json(cleaned)),
            ]:
                if not candidate:
                    continue
                try:
                    parsed = json.loads(candidate)
                    break
                except json.JSONDecodeError as e:
                    parse_errors.append(f"{stage_name}: {e}")
            if parsed is None:
                last_err = LlmJsonParseFailed(
                    f"JSON 解析失败(3 级 fallback 全失败):"
                    f"{' | '.join(parse_errors)};"
                    f"raw 前 200 字={raw[:200]!r}"
                )
                if attempt < retries:
                    continue
                raise last_err
            return parsed, usage

        except (LlmCallFailed, LlmJsonParseFailed):
            raise
        except Exception as e:
            last_err = e
            # CT-OPT.3:429 限流时指数退避 1-8s,其他错误立即重试
            if attempt < retries:
                if _is_rate_limit_error(e):
                    sleep_s = _backoff_sleep_seconds(attempt)
                    logger.warning(
                        f"{vendor_label} LLM 限流(429),退避 {sleep_s:.1f}s 后重试 "
                        f"(attempt={attempt + 1}/{retries})"
                    )
                    time.sleep(sleep_s)
                continue
            raise LlmCallFailed(
                f"{vendor_label} LLM 调用失败({type(e).__name__}):{e}"
            ) from e

    raise LlmCallFailed(f"{vendor_label} LLM 调用所有重试都失败:{last_err}")


def _openai_compat_call_text(
    system_prompt: str,
    user_input: dict | str,
    *,
    api_key: str,
    api_base: str,
    model: str,
    vendor_label: str,
    max_tokens: int = 8000,
    temperature: float = 0.65,
    retries: int = 1,
    timeout: float = 120.0,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
) -> tuple[str, dict]:
    """OpenAI 兼容端点的纯文本调用(composer / narrative 用)。

    frequency_penalty / presence_penalty(提案 B,2026-05-24):见 _openai_compat_call_json 注释。
    """
    from openai import OpenAI

    if not api_key:
        raise LlmCallFailed(
            f"{vendor_label} API key 未配置(请在项目根 .env 填对应字段)"
        )
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as e:
        raise LlmCallFailed(
            f"{vendor_label} API key 含非 ASCII 字符(可能是占位符):{e}"
        ) from e

    client = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
    user_content = (
        json.dumps(user_input, ensure_ascii=False, indent=2)
        if isinstance(user_input, dict)
        else user_input
    )

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
            )
            raw = resp.choices[0].message.content or ""
            usage = {
                "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            }
            return _strip_markdown_fence(raw), usage

        except LlmCallFailed:
            raise
        except Exception as e:
            last_err = e
            # CT-OPT.3:429 限流时指数退避 1-8s,其他错误立即重试
            if attempt < retries:
                if _is_rate_limit_error(e):
                    sleep_s = _backoff_sleep_seconds(attempt)
                    logger.warning(
                        f"{vendor_label} LLM 限流(429),退避 {sleep_s:.1f}s 后重试 "
                        f"(attempt={attempt + 1}/{retries})"
                    )
                    time.sleep(sleep_s)
                continue
            raise LlmCallFailed(
                f"{vendor_label} LLM 调用失败({type(e).__name__}):{e}"
            ) from e

    raise LlmCallFailed(f"{vendor_label} LLM 调用所有重试都失败:{last_err}")


# ============================================================
# 公开 API — Sprint D.8 改为 thin wrapper 委托给 router
# ============================================================
#
# 老调用方代码全部命中这里 → 内部走 llm_routing.router → 实际 adapter 由 vendor 决定。
# 这层 wrapper 必须留着,且签名严格不变,否则 service 层会编译失败(20+ 处调用)。

# ============================================================
# BYOK 自携密钥拦截(2026-06-05)
# ============================================================
#
# 设计:
#   - call_llm_json/text 顶部查 BYOK 配置 — 有则绕过 adapter 直接用用户 key
#   - user_id 三档来源:① 显式参数 ② ContextVar(endpoint 自动 set)③ None = 平台默认
#   - 任何异常(BYOK 查询失败 / 解密失败)→ logger 记录 + 回退到平台默认,绝不阻断 LLM 调用
#
# 性能:
#   - 每次 LLM 调用开一次 sqlite 连接查 byok_subscriptions + byok_configs(都有索引)
#   - 主创作 1 sim ≈ 200 次 LLM 调用,200 次 sqlite 查询(每次 < 1ms)成本可忽略
#   - 后续可加 per-request cache(若发现 bottleneck)
#
# 不影响范围:
#   - 测试 monkeypatch call_llm_json/text 直接的:照常工作(走快路径)
#   - 没登录态的调用(后台 cron / migration):user_id=None,走平台默认

def _resolve_byok_llm_config(user_id: str | None) -> dict | None:
    """查 BYOK 配置 — 返 None 表示该调用应走平台默认。

    user_id 来源优先级:
      1. 显式传入(测试 / 显式注入)
      2. ContextVar(endpoint Depends 自动设置)
      3. None → 直接返 None

    异常静默 + 日志(BYOK 故障绝不阻断 LLM 调用)。
    """
    if user_id is None:
        from app.services.byok_context import get_current_user_id
        user_id = get_current_user_id()

    if not user_id:
        return None

    try:
        from app.db import get_connection
        from app.services import byok_service

        conn = get_connection()
        try:
            cfg = byok_service.get_active_llm_config(conn, user_id)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return cfg
    except Exception:  # noqa: BLE001
        # BYOK 查询失败 → fallback 平台默认。日志但不抛。
        logger.exception(
            "BYOK 查询失败,fallback 到平台默认 LLM(user_id=%s)",
            user_id,
        )
        return None


def _require_platform_budget(user_id: str | None) -> None:
    """走【平台默认 key】前的余额闸(item7 止血,2026-06-26)。

    背景:refine / 剧创态全子系统等入口过去调 AI 前完全不查余额 → 0 余额用户
    一路白嫖平台公用 key → 创始人 API 账户欠费。这里在 LLM 客户端**单一咽喉**统一拦:
    能走到平台默认路径说明 BYOK 未命中(用的是平台 key),必须有余额。

    放行规则(可用性优先,宁漏放系统调用也不误杀):
      - user_id 无(后台 cron / migration / 未登录)→ 放行
      - founder 档 → 放行(无限)
      - 余额 total > 0 → 放行
      - 否则抛 InsufficientCredits(main.py 全局 handler 转 429 + 引导升级/BYOK)
    BYOK 用户走不到这里(call_llm_* 上游 byok_cfg 命中已 return,用自己的 key,不占平台额度)。
    """
    if user_id is None:
        try:
            from app.services.byok_context import get_current_user_id
            user_id = get_current_user_id()
        except Exception:  # noqa: BLE001
            user_id = None
    if not user_id:
        return
    from app.config import settings
    from app.services.credit_service import InsufficientCredits, get_balance
    try:
        from app.db import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT email FROM users WHERE id=?", (user_id,)
            ).fetchone()
            # founder 判定走邮箱白名单 —— DB users.plan 永远不是 'founder'(CHECK 约束),
            # 'founder' 是 deps.get_current_user 的内存态。必须对齐 credit_service 逻辑。
            if (
                row is not None
                and row["email"]
                and row["email"].lower() in settings.founder_emails
            ):
                return
            total = get_balance(conn, user_id).total
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return  # 查询失败放行(可用性优先,不因基础设施抖动误杀创作)
    if total <= 0:
        raise InsufficientCredits(needed=1, available=total, action="ai_call")


def call_llm_json(
    system_prompt: str,
    user_input: dict | str,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.6,
    retries: int = 2,
    timeout: float = 60.0,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    user_id: str | None = None,
) -> tuple[Any, dict]:
    """调用文本 LLM,返回 (parsed_json, usage_dict)。

    Sprint D.8:委托给 llm_routing.router.get_text_llm()。
    默认 vendor='deepseek',与改造前行为完全一致。

    frequency_penalty / presence_penalty(提案 B,2026-05-24):
      默认 0.0(向后兼容,不开启)— narrator / agent_dialogue 等长输出场景
      显式传 0.3~0.5 治"n-gram 原文复读"。

    2026-06-05 BYOK:
      - user_id 默认 None → 从 ContextVar 读(endpoint Depends 自动设置)
      - 显式传 user_id 可在测试 / 后台任务覆盖
      - 有 BYOK 配置 → 用用户 base_url / model / key;无则平台默认
    """
    # ---- BYOK 拦截 ----
    byok_cfg = _resolve_byok_llm_config(user_id)
    if byok_cfg:
        return _openai_compat_call_json(
            system_prompt,
            user_input,
            api_key=byok_cfg["api_key"],
            api_base=byok_cfg["base_url"],
            model=byok_cfg["model_name"],
            vendor_label=f"BYOK-{byok_cfg['provider']}",
            max_tokens=max_tokens,
            temperature=temperature,
            retries=retries,
            timeout=timeout,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )

    # ---- 平台默认路径 ----
    # item7:走平台 key 前先查余额,0 余额不放行(防白嫖创始人 API)
    _require_platform_budget(user_id)
    # 在函数体内 import,避免与 llm_routing.protocols 形成循环 import
    from app.services.llm_routing.router import get_text_llm

    return get_text_llm().call_json(
        system_prompt,
        user_input,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
        timeout=timeout,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
    )


def call_llm_text(
    system_prompt: str,
    user_input: dict | str,
    *,
    max_tokens: int = 8000,
    temperature: float = 0.65,
    retries: int = 1,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    user_id: str | None = None,
) -> tuple[str, dict]:
    """调用文本 LLM,返回 (text, usage_dict)。

    Sprint D.8:委托给 llm_routing.router.get_text_llm()。

    frequency_penalty / presence_penalty(提案 B,2026-05-24):见 call_llm_json 注释。
    2026-06-05 BYOK:见 call_llm_json 注释。
    """
    # ---- BYOK 拦截 ----
    byok_cfg = _resolve_byok_llm_config(user_id)
    if byok_cfg:
        return _openai_compat_call_text(
            system_prompt,
            user_input,
            api_key=byok_cfg["api_key"],
            api_base=byok_cfg["base_url"],
            model=byok_cfg["model_name"],
            vendor_label=f"BYOK-{byok_cfg['provider']}",
            max_tokens=max_tokens,
            temperature=temperature,
            retries=retries,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )

    # ---- 平台默认路径 ----
    # item7:走平台 key 前先查余额,0 余额不放行(防白嫖创始人 API)
    _require_platform_budget(user_id)
    from app.services.llm_routing.router import get_text_llm

    return get_text_llm().call_text(
        system_prompt,
        user_input,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
    )


def estimate_cost_yuan(input_tokens: int, output_tokens: int) -> float:
    """成本估算 — Sprint D.8 改为查 llm_routing.pricing 配置表。

    签名保持(in, out),内部按当前 text_vendor + llm_model 查 token 单价。
    未来 image / vision adapter 直接调 pricing.lookup_price(unit_type=...).
    """
    from app.config import settings
    from app.services.llm_routing.pricing import lookup_price

    return lookup_price(
        settings.text_vendor,
        settings.llm_model,
        "token",
        input_tokens,
        output_tokens,
    )
