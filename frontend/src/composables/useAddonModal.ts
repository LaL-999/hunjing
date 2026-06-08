/**
 * useAddonModal — 全局 singleton 控制加购包 modal(Sprint C.1)。
 *
 * 用 singleton 模式(同 useUpgradeModal),let QuotaIndicator / SimulationDock 等
 * 任意位置都能 open。AppSidebar / App.vue 全局挂一份 <AddonPurchaseModal />。
 *
 * 触发时机:
 *   - QuotaIndicator "+ 加购" 按钮
 *   - SimulationDock / 漫画态等 InsufficientCredits 弹"是否加购"二级 CTA(C.2 接通后)
 *   - 用户菜单"加购 credit"链接(后续可加)
 */
import { ref } from "vue";

const isOpen = ref(false);

export function useAddonModal() {
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
