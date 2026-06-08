/**
 * useLoginModal — 全局 singleton 登录模态状态。
 *
 * 任何组件都可以 import 调 open() / close() 控制 LoginModal。
 * 不进 Pinia(过于简单 + 无业务数据,YAGNI)。
 *
 * 用法:
 *   const loginModal = useLoginModal();
 *   loginModal.open();        // 触发登录
 *   loginModal.open('/projects/abc');   // 登录成功后跳到指定路径
 */
import { ref } from "vue";

const isOpen = ref(false);
const redirectAfter = ref<string | null>(null);

export function useLoginModal() {
  return {
    isOpen,
    open(redirectTo?: string) {
      redirectAfter.value = redirectTo ?? null;
      isOpen.value = true;
    },
    close() {
      isOpen.value = false;
      redirectAfter.value = null;
    },
    /** 登录成功后由 LoginModal 调用 */
    consumeRedirect(): string | null {
      const r = redirectAfter.value;
      redirectAfter.value = null;
      return r;
    },
  };
}
