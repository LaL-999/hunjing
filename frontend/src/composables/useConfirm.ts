/**
 * useConfirm — 全平台二次确认弹窗 singleton + Promise API。
 *
 * 替代 window.confirm():
 *   - 原生 confirm 顶部弹出无背景,体验割裂
 *   - 这里是中央弹出 + 背景虚化 + 项目浅色 token,与其它 modal 对齐
 *
 * 使用:
 *   const ok = await confirm({
 *     title: "删除「林婉」?",
 *     message: "关联的关系会一并清除",
 *     danger: true,           // 红色「确定」按钮(删除场景默认 true)
 *     confirmLabel: "删除",   // 可选,默认「确定」
 *     cancelLabel: "取消",    // 可选,默认「取消」
 *   });
 *   if (!ok) return;
 *
 * 设计:
 *   - 全局 singleton state(避免每组件挂 dialog 实例)
 *   - Promise<boolean> resolve 模型,与原生 confirm 形态对齐 → 改造代码最少
 *   - 同时 open 第二个 confirm 时,前一个 Promise 自动 resolve(false)防卡死
 *   - 模板由 App.vue 全局挂一次 ConfirmDialog
 */
import { ref } from "vue";

export interface ConfirmOptions {
  /** 标题(可选,例:"删除「林婉」?")*/
  title?: string;
  /** 正文,必填(可多行,\n 渲染为换行)*/
  message: string;
  /** 是否危险操作(确定按钮变红)— 删除 / 不可恢复操作建议 true */
  danger?: boolean;
  /** 自定义确定按钮文案,默认「确定」*/
  confirmLabel?: string;
  /** 自定义取消按钮文案,默认「取消」*/
  cancelLabel?: string;
}

interface InternalState extends ConfirmOptions {
  /** 当前 promise 的 resolver — open 时设,close 时 resolve + 清空 */
  resolver: ((ok: boolean) => void) | null;
}

const state = ref<InternalState>({
  message: "",
  resolver: null,
});
const isOpen = ref(false);

/**
 * 弹一个确认对话框,返回 Promise<boolean>。
 * 用户点「确定」→ true;点「取消」/ Esc / 点 backdrop → false。
 */
export function confirm(opts: ConfirmOptions): Promise<boolean> {
  // 防御:如果上一个 confirm 还没关就开新的,先 resolve(false) 释放上一个
  if (state.value.resolver) {
    state.value.resolver(false);
  }
  return new Promise<boolean>((resolve) => {
    state.value = {
      title: opts.title,
      message: opts.message,
      danger: opts.danger ?? false,
      confirmLabel: opts.confirmLabel ?? "确定",
      cancelLabel: opts.cancelLabel ?? "取消",
      resolver: resolve,
    };
    isOpen.value = true;
  });
}

/** 内部用:ConfirmDialog 组件按确定按钮 */
function _resolve(ok: boolean) {
  const r = state.value.resolver;
  state.value.resolver = null;
  isOpen.value = false;
  if (r) r(ok);
}

export function useConfirm() {
  return {
    isOpen,
    state,
    confirm,
    onConfirm: () => _resolve(true),
    onCancel: () => _resolve(false),
  };
}
