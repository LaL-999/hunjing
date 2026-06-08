/**
 * useNewProjectModal — 全局 singleton 控制"新建项目"模态。
 *
 * Sprint 2.A.F:加 mode state — 用户在 dashboard 选了态后,带 mode 触发 modal,
 * NewProjectModal 据此显态 chip + 流程提示;创建成功 emit(project, mode)
 * 让父组件按 mode 决定后续引导。
 *
 * Sprint 3.A 末尾态完工后,实际 open 调用接受 initial / middle / end;
 * cycle 仍在 CreationModeQuadrant.handleClick 内被 toast 拦截(未到 newProjectModal)。
 * mode 类型保持完整与后端 ProjectMode schema 对齐,方便 3.B 周期态完工时一行解锁。
 */
import { ref } from "vue";

import { type ProjectMode } from "../api/types";

const isOpen = ref(false);
const mode = ref<ProjectMode>("initial");

export function useNewProjectModal() {
  return {
    isOpen,
    mode,
    open(m: ProjectMode = "initial") {
      mode.value = m;
      isOpen.value = true;
    },
    close() {
      isOpen.value = false;
    },
  };
}
