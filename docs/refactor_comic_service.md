# refactor_comic_service.md — comic_service.py 拆分设计

**状态**:待执行(标记 TODO,上线后处理)
**创建**:2026-06-02
**目标 sprint**:上线后第 1-2 个 sprint

## 背景

`backend/app/services/comic_service.py` 已经 **3815 行**,职责混杂:
- Orchestrator(状态机调度 + `_RUNNING_COMICS` 注册表)
- 10 个 agent 实现(screenwriter / style director / character anchor / visual assets / director / image generator / visual QA / inpainter / typesetter)
- 计费 + 退款 + 配额逻辑
- Cron 任务(过期清理 / 重试)
- 工具函数(失败格占位 / SVG 生成 等)

**上线前夕(2026-06-02)不拆**:理由是大重构在上线前夕引入未知 bug 的风险远高于"代码可维护性"收益。当前 vue-tsc 0 错 / pytest 902 passed 是稳定基线,不应破坏。

**上线后拆**:本文档为后续拆分提供蓝图。

## 拆分目标

| 模块 | 职责 | 估算行数 |
|---|---|---|
| `comic_orchestrator.py` | 状态机 / kick_off / `_RUNNING_COMICS` 注册表 / done / failed / cancelled 转移 | < 400 |
| `comic_panel_generator.py` | 并行生图主流程 / 视觉资产管理 / 单 panel retry / Visual QA | < 1500 |
| `comic_typesetter_service.py` | 排版(PIL 对话气泡 / 旁白 / 拟声词) / 缓存 / push | < 800 |
| `comic_credit_service.py` | 计费 / 退款 / quota 校验 | < 400 |
| `comic_agents.py`(可选) | 10 agent 实现集中 | < 1500 |
| `comic_service.py`(facade) | 重新 export 所有公共 API,**调用方零修改** | < 100 |

## 拆分原则

1. **零调用方修改**:`comic_service.py` 保留为 facade,所有公共函数从子模块 `import` 再 re-export。`from app.services.comic_service import create_comic` 这种调用必须仍然工作。

2. **不拆 mutable shared state**:`_RUNNING_COMICS` / `_RUNNING_LOCK` 等全局状态留在 orchestrator,其它模块通过 import 引用。

3. **按"输入 → 输出"切分,不按"agent 序号"切分**:
   - 不要按 #1-#10 agent 名字拆 — 它们互相调用复杂
   - 按"用户视角的产品流程"切:创建 sim → 生 panel → 排版输出

4. **逐步迁移,不一次切**:
   - 先抽 `comic_typesetter_service.py`(最独立,纯 PIL 无 LLM 依赖)
   - 再抽 `comic_credit_service.py`(纯函数,无并发)
   - 最后抽 `comic_panel_generator.py`(最复杂,放最后)
   - 每抽一个跑全套 pytest

5. **配套测试**:每个新模块独立可测,不需要起整个 comic 流水线。

## 风险

- **隐式依赖**:某些函数访问 `__name__` / `globals()` / monkey-patch,拆分时 break。
- **循环导入**:facade 必须用 `from .comic_panel_generator import xxx` 而不是 `from .comic_service import xxx`。
- **测试 fixture**:`patched_comic_llm` 类似 fixture 可能依赖具体 module path。

## 执行步骤(建议)

1. 用 `pyan3` / `pydeps` 画依赖图,识别耦合点
2. 写 `test_comic_typesetter.py` 把现有 typesetter 函数封死(锚点测试)
3. 抽 typesetter → 跑测试
4. 抽 credit → 跑测试
5. 抽 orchestrator(保留 facade)→ 跑测试
6. 最后抽 panel_generator
7. 删除 `comic_service.py` 里已迁移的代码,保留 re-export
8. 跑全套 pytest + 漫画端到端实测

## 验收标准

- 全套 pytest 0 失败
- 漫画态端到端实测正常(创建 → 生 panel → 排版 → push)
- `comic_service.py` < 100 行(仅 facade)
- 每个新模块 < 1500 行
- 没有循环导入
- 调用方零修改
