/**
 * labels.ts — insights-frontend 字段翻译表(INS-A9,2026-05-27 末³).
 *
 * 用途:把 analytics.db 里的 raw 枚举字面量(initial / page_view / ai_call_done)
 * 翻译成主平台口径的人话(初创态 / 打开页面 / AI 完成调用).
 *
 * 范本:对齐主平台 frontend/src/views/ProjectView.vue MODE_LABEL_MAP.
 *
 * 加新枚举时:
 *   - 后端 insights-backend/app/models/event.py EVENT_TYPES 改了 → 这里同步
 *   - 主平台 ProjectMode 改了 → 这里同步
 *   - 缺译时 formatLabel() fallback 返 raw 字符串(不抛错)
 */

// ============================================================
// 模式(创作态)
// ============================================================
//
// 主平台数据库字面量是 `cycle` (向后兼容),用户可见文案是"漫创态".
// 见 feedback_collab_style.md 5.0.

export const MODE_LABELS: Record<string, string> = {
  initial: "初创态",
  middle: "中段态",
  tail: "收尾态",
  comic: "漫创态",
  cycle: "漫创态", // 主平台数据库历史字面量
};

// ============================================================
// 事件类型(对齐 insights-backend EVENT_TYPES)
// ============================================================

export const EVENT_TYPE_LABELS: Record<string, string> = {
  // 页面生命周期
  page_view: "打开页面",
  page_leave: "离开页面",
  page_refresh: "刷新页面",
  // 会话
  session_start: "开始会话",
  session_end: "结束会话",
  // AI 调用
  ai_call_start: "AI 开始调用",
  ai_call_done: "AI 完成调用",
  ai_call_failed: "AI 调用失败",
  ai_call_error: "AI 调用失败",
  // 创作行为
  mode_switch: "切换创作态",
  project_create: "创建项目",
  project_delete: "删除项目",
  simulation_create: "创建推演",
  simulation_start: "启动推演",
  simulation_done: "推演完成",
  // 审计
  audit_run: "跑自洽审计",
  canonical_audit_run: "跑正典守护者",
  // 异常
  error: "前端错误",
};

// ============================================================
// 步骤(创作流程内部的细分阶段)
// ============================================================

export const STEP_LABELS: Record<string, string> = {
  step1_outline: "步骤 1 · 大纲",
  step2_draft: "步骤 2 · 初稿",
  step3_audit: "步骤 3 · 审稿",
  step4_evolve: "步骤 4 · 演化",
  // 兼容用主平台 step 命名
  outline: "大纲",
  draft: "初稿",
  audit: "审稿",
  evolve: "演化",
};

// ============================================================
// Helpers
// ============================================================

/**
 * 通用翻译入口:有翻译就返人话,没有就返 raw(不抛错).
 *
 * 用法:
 *   formatLabel(MODE_LABELS, "initial") → "初创态"
 *   formatLabel(MODE_LABELS, "unknown") → "unknown"
 *   formatLabel(MODE_LABELS, null) → "—"
 */
export function formatLabel(
  table: Record<string, string>,
  key: string | null | undefined,
  fallback = "—",
): string {
  if (key === null || key === undefined || key === "") return fallback;
  return table[key] ?? key;
}

export function formatMode(key: string | null | undefined): string {
  return formatLabel(MODE_LABELS, key);
}

export function formatEventType(key: string | null | undefined): string {
  return formatLabel(EVENT_TYPE_LABELS, key);
}

export function formatStep(key: string | null | undefined): string {
  return formatLabel(STEP_LABELS, key);
}
