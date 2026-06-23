/**
 * usePayment — 统一支付弹窗单例(2026-06-09 商业化重塑)。
 *
 * 范式同 useConfirm / useToast:模块级单例 state,任意组件调 open(skuCode)
 * 即唤起全局挂载的 <PaymentModal />。所有"购买 / 升级 / 开通 / 加购"按钮
 * 都收敛到这一个入口,不再各自实现支付 UI。
 *
 * 用法:
 *   const pay = usePayment();
 *   pay.open("sub_pro_monthly");        // 订阅 Pro 月付
 *   pay.open("byok_1m");                // BYOK 月卡
 *   pay.open("credit_medium");          // 配额中包
 *
 *   // App.vue 挂一次:<PaymentModal />
 */
import { ref } from "vue";

const isOpen = ref(false);
const currentSkuCode = ref<string | null>(null);
// 履约成功回调(可选)— 购买方想在发货后刷新自己的状态时传
const onFulfilledCb = ref<(() => void) | null>(null);

export interface OpenPaymentOptions {
  /** 履约成功(订单 paid 且发货)后回调,用于刷新配额 / 订阅 / BYOK 状态 */
  onFulfilled?: () => void;
}

export function usePayment() {
  function open(skuCode: string, opts?: OpenPaymentOptions): void {
    currentSkuCode.value = skuCode;
    onFulfilledCb.value = opts?.onFulfilled ?? null;
    isOpen.value = true;
  }
  function close(): void {
    isOpen.value = false;
    currentSkuCode.value = null;
    onFulfilledCb.value = null;
  }
  function notifyFulfilled(): void {
    if (onFulfilledCb.value) {
      try {
        onFulfilledCb.value();
      } catch {
        /* 回调异常不影响支付流程 */
      }
    }
  }
  return {
    isOpen,
    currentSkuCode,
    open,
    close,
    notifyFulfilled,
  };
}
