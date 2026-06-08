"""OTP 邮箱通道(SMTP_SSL)— Sprint 1.B 唯一登录通道。

错误分两类:
  SmtpNotConfigured — .env 里 SMTP_USER/PASS/FROM 缺,启动期问题
  SmtpSendFailed    — SMTP 实际发送失败(认证 / 网络 / 配额)

测试用:test 时可以 monkey patch send_email_otp 避免真发邮件。
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import settings


class SmtpNotConfigured(Exception):
    """.env 缺 SMTP_USER / SMTP_PASS / SMTP_FROM 之一。"""


class SmtpSendFailed(Exception):
    """SMTP 发送实际失败(网络 / 认证 / 配额)。"""


def send_email_otp(target_email: str, code: str) -> None:
    if not settings.smtp_configured():
        raise SmtpNotConfigured(
            "SMTP 凭据未配置。请在项目根 .env 填写 "
            "HUIMENG_SMTP_USER / HUIMENG_SMTP_PASS / HUIMENG_SMTP_FROM。"
            "QQ 邮箱授权码获取:邮箱设置 → 账号 → POP3/IMAP/SMTP → 开启服务 → 生成授权码。"
        )

    msg = EmailMessage()
    msg["Subject"] = f"浑晶登录验证码:{code}"
    msg["From"] = settings.smtp_from
    msg["To"] = target_email
    msg.set_content(
        f"你正在登录浑晶 (HunJing)。\n\n"
        f"验证码:{code}\n\n"
        f"有效期 5 分钟。\n"
        f"如果不是你本人操作,请忽略此邮件。\n\n"
        f"--\n浑晶 · 让每一个意难平,都有一个版本"
    )

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise SmtpSendFailed(
            f"SMTP 认证失败,检查 USER/PASS(QQ 邮箱要用授权码不是登录密码):{e}"
        ) from e
    except smtplib.SMTPException as e:
        raise SmtpSendFailed(f"SMTP 发送失败:{e}") from e
    except OSError as e:
        raise SmtpSendFailed(f"SMTP 连接失败(网络 / 端口 / 防火墙):{e}") from e
