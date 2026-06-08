/**
 * useDocumentViewer — 全局 singleton 控制 DocumentViewer modal。
 *
 * 任意组件调 useDocumentViewer().open() 弹查看器。
 * 协议已合并为 1 个,无需 docKey 参数。
 */
import { ref } from "vue";

const isOpen = ref(false);

export function useDocumentViewer() {
  return {
    isOpen,
    open() {
      isOpen.value = true;
    },
    close() {
      isOpen.value = false;
    },
  };
}
