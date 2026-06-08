"""
BYOK 自携密钥 — API key 对称加密 / 解密。

设计:
- 用 cryptography.fernet(AES-128-CBC + HMAC-SHA256,内含完整性校验)
- 密钥从环境变量 BYOK_ENCRYPTION_KEY 读(开发环境从 .env;生产环境从部署平台 secret)
- env 中存的是 base64 编码后的 32 字节密钥(Fernet.generate_key() 生成的标准格式)

为什么不用 RSA / ed25519:
- API key 是用户的高价值长串字符串(sk-xxx, 约 30-50 字节)
- 对称加密足够 — 我们既是加密方又是解密方(单进程内来回)
- AES-128-CBC + HMAC 在 sqlite 存储里只增加约 1.3x 体积,优秀

mask 算法:
- 输入 "sk-abc123def456ghi789"(20 字节)
- 输出 "sk-abc12...gh789"(前 5 后 5,中间省略)
- 太短的(<10 字节)直接全 mask "*****"

测试:
- 加密一次,解密能得回原文
- 不同密钥加密的密文,用本机密钥解不开(InvalidToken 异常)
- mask 不暴露中间字段
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


_CACHED_FERNET: Optional[Fernet] = None


# ============================================================
# 密钥管理
# ============================================================

def _load_master_key() -> bytes:
    """
    从 env 读 BYOK 主密钥。

    格式:Fernet 标准 base64(44 字符);若不存在或非法,fallback 到本地开发用 default。
    生产环境必须显式设 — 不设会用 default,日志告警。
    """
    raw = os.environ.get("BYOK_ENCRYPTION_KEY", "").strip()

    if not raw:
        # 本地开发兜底 — 固定 default key,数据库中已加密的 BYOK config 跨重启可读
        # 注意:生产部署必须显式 set BYOK_ENCRYPTION_KEY,否则有泄密风险
        default_dev_key = b"-vT2YxRk9zmJ8FN3aQpL6cH4dB1xWnK0sZyA7uPgIeQ="
        logger.warning(
            "BYOK_ENCRYPTION_KEY 未设置 — 使用 dev fallback。生产环境务必设置此 env!"
        )
        return default_dev_key

    # Fernet key 长度应为 44 字符的 urlsafe-base64
    try:
        key_bytes = raw.encode("ascii")
        # 校验:Fernet 构造会 raise 如果格式不对
        Fernet(key_bytes)
        return key_bytes
    except Exception as exc:
        logger.error(
            "BYOK_ENCRYPTION_KEY 格式非法(应为 Fernet.generate_key() 输出的 44 字符 base64): %s",
            exc,
        )
        # 仍然不能让服务挂,fallback 到 default
        default_dev_key = b"-vT2YxRk9zmJ8FN3aQpL6cH4dB1xWnK0sZyA7uPgIeQ="
        return default_dev_key


def _get_fernet() -> Fernet:
    """单例 Fernet,避免每次重建。"""
    global _CACHED_FERNET
    if _CACHED_FERNET is None:
        _CACHED_FERNET = Fernet(_load_master_key())
    return _CACHED_FERNET


def reset_fernet_cache() -> None:
    """测试用 — 强制重新读 env(改了 BYOK_ENCRYPTION_KEY 后)。"""
    global _CACHED_FERNET
    _CACHED_FERNET = None


# ============================================================
# 加密 / 解密 API
# ============================================================

def encrypt_api_key(plaintext: str) -> str:
    """
    加密用户 API key。

    输入:plaintext str(用户填写的原始 key,通常 "sk-xxx" 形式)
    输出:Fernet 密文 str(base64 编码,~100-200 字节,可直接存 sqlite TEXT)

    抛:无(空字符串也能加密 — 但业务层应在调用前过滤)
    """
    if plaintext is None:
        raise ValueError("不能加密 None,业务层应在调用前校验")
    fernet = _get_fernet()
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """
    解密 API key 密文。

    输入:ciphertext(数据库里存的 Fernet token)
    输出:原始 plaintext

    抛:cryptography.fernet.InvalidToken — 密文非法 / 主密钥不对 / token 损坏
    业务层应捕获并返回 502(密钥配置出错,请联系客服)
    """
    if not ciphertext:
        raise ValueError("不能解密空字符串")
    fernet = _get_fernet()
    try:
        plaintext_bytes = fernet.decrypt(ciphertext.encode("ascii"))
    except InvalidToken:
        # 重抛,业务层处理 — 日志在这里记一次(因为重抛后调用栈可能截断)
        logger.error("BYOK 密文解密失败 — InvalidToken。可能主密钥变了 / 密文损坏")
        raise
    return plaintext_bytes.decode("utf-8")


# ============================================================
# mask 展示用
# ============================================================

def mask_api_key(plaintext: str) -> str:
    """
    生成展示用的脱敏字符串。

    规则:
    - 长度 < 10:全 mask "*****"
    - 长度 ≥ 10:前 5 + ... + 后 5

    例子:
    - "sk-abc123def456ghi789xyz" → "sk-ab...89xyz"
    - "short"                    → "*****"
    - "abcdefghij" (10)          → "abcde...fghij"  (前5+后5,中间...)
    """
    if not plaintext:
        return ""
    length = len(plaintext)
    if length < 10:
        return "*****"
    return f"{plaintext[:5]}...{plaintext[-5:]}"
