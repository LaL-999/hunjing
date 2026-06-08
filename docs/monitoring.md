# 浑晶 监控方案

**目标**:用户报问题之前我先看到问题。

## 三层监控

| 层级 | 工具 | 用途 |
|---|---|---|
| **错误追踪** | Sentry(推荐)/ 自建 logstash | 任何 5xx / 前端 uncaught error |
| **可用性** | UptimeRobot / Better Stack | 每分钟探活,挂了立刻 push 通知 |
| **业务指标** | 自建简单 dashboard | LLM 消耗 / 用户活跃 / credit 异常 |

---

## 1. Sentry 接入(20 分钟工作量)

### 后端
```bash
pip install "sentry-sdk[fastapi]>=2.0"
```

`backend/app/main.py` 顶部加:
```python
import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

if dsn := os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=dsn,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,  # 10% 采样,省 quota
        environment=os.getenv("ENV", "production"),
        release=os.getenv("APP_VERSION", "v1.0.0"),
    )
```

`.env` 加:
```bash
SENTRY_DSN=https://xxxx@xxx.ingest.sentry.io/yyyy
ENV=production
```

### 前端
```bash
npm install --save @sentry/vue
```

`frontend/src/main.ts` 加:
```typescript
import * as Sentry from "@sentry/vue";

if (import.meta.env.PROD && import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    app,
    dsn: import.meta.env.VITE_SENTRY_DSN as string,
    tracesSampleRate: 0.1,
    environment: "production",
    release: __APP_VERSION__,
  });
}
```

vite.config.ts 加 define:
```typescript
define: { __APP_VERSION__: JSON.stringify("v1.0.0") }
```

---

## 2. UptimeRobot 探活

设 2 个监控:
- **frontend**:`https://shuangdayeye.cn/` → 200
- **backend health**:`https://shuangdayeye.cn/api/health`(已加 — 见 backend/app/main.py)

通知方式:邮件 + 微信(Server 酱)双通道。

---

## 3. 业务指标看板(轻量,不用 Grafana)

### 必看 4 个指标
1. **每小时 LLM 调用次数**(SQL:`SELECT COUNT(*) FROM credit_transactions WHERE action LIKE '%continuation%' AND created_at > datetime('now','-1 hour')`)
2. **每日新增用户**(`SELECT COUNT(*) FROM users WHERE created_at > date('now','-1 day')`)
3. **每日推演完成数**(`SELECT COUNT(*) FROM simulations WHERE state='done' AND completed_at > date('now','-1 day')`)
4. **当前余额排行 top 10**(看是否有用户疯狂消耗 → 可能是恶意)

### 接入方式
最简单:写个 `backend/app/routers/admin.py`,加 `/admin/metrics` 端点,鉴权用一个 hardcoded admin token,输出上面 4 个指标。然后用 cron + curl 每小时打到企业微信 / Telegram。

(浑晶已经有 `insights-backend` 子系统,如果它在用,把这些指标加进去就行。)

---

## 4. 关键告警阈值(建议初版)

| 指标 | 阈值 | 告警通道 |
|---|---|---|
| 5xx 错误率 | 5 分钟 ≥ 10 次 | 企业微信 + 短信 |
| backend down(UptimeRobot)| 探活失败 2 次 | 短信 + 邮件 |
| DeepSeek 余额 | < 50 元 | 邮件(每天 1 次,防扰)|
| 当日推演完成 = 0(用户问问题前) | 跨日还是 0 | 邮件 |
| 单用户每小时推演 ≥ 20 次 | 命中 | 企业微信(看是否压测 / 恶意) |

---

## 5. 日志策略

- **生产**:`logging.WARNING` 以上写文件(`logs/huimeng-YYYY-MM-DD.log`),`INFO` 及以下走 stdout 让 systemd journal 收
- **logrotate**:每天切割,保留 30 天
- **关键事件**:登录 / 创建推演 / 删除项目 / 退款 这些**必须**有 log(已有,代码里 `log.info(...)` 调用)
- **Sentry**:`WARNING` 及以上自动上报

---

## 6. 上线后第一周看什么

第 1 天:
- 5 分钟刷一次 Sentry,看是否有重复出现的错
- 主要看登录链路(OTP / JWT 过期 / CORS)

第 1 周:
- 每天看 5xx 错误率
- 每天看 credit 异常(消耗 / 退款)
- 每天看用户反馈

如果一周没有严重 issue → 告警阈值放宽,每周看一次即可。
