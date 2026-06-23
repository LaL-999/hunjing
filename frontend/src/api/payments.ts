/**
 * 统一支付 API client — 商业化重塑第一期(2026-06-09)。
 *
 * 所有付费场景(订阅 / BYOK / 配额包)共用这套订单接口。前端任何"购买"按钮
 * 都通过 usePayment().open(skuCode) 唤起统一 PaymentModal,内部调这里。
 */
import { api } from "./client";

export type PaymentCategory = "subscription" | "byok" | "credit";

export type OrderStatus =
  | "pending"        // 待付(显示二维码)
  | "submitted"      // 已传凭证待审
  | "paid"           // 已确认
  | "rejected"       // 驳回
  | "manual_review"  // 需人工
  | "expired"
  | "refunded";

export interface PaymentSku {
  code: string;
  title: string;
  category: PaymentCategory;
  amount_cents: number;
  amount_yuan: number;
  quantity: number;
  meta: Record<string, unknown>;
}

export interface PaymentOrder {
  id: string;
  user_id: string;
  sku_code: string;
  sku_title: string;
  category: PaymentCategory;
  amount_cents: number;
  amount_yuan: number;
  quantity: number;
  channel: string;
  status: OrderStatus;
  proof_image_path: string | null;
  proof_submitted_at: string | null;
  rejected_reason: string | null;
  fulfilled_at: string | null;
  fulfillment_ref: string | null;
  fulfillment?: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  sku_meta?: Record<string, unknown>;
}

export interface PayInfo {
  channel: string;
  payee_name: string;
  wechat_qr_url: string;
  alipay_qr_url: string;
  note: string;
}

/** 上架 SKU 列表(定价页 / 购买弹窗)*/
export async function fetchCatalog(category?: PaymentCategory): Promise<PaymentSku[]> {
  const q = category ? `?category=${encodeURIComponent(category)}` : "";
  const r = await api.get<{ items: PaymentSku[] }>(`/payments/catalog${q}`);
  return r.items ?? [];
}

/** 收款二维码 + 收款方信息 */
export async function fetchPayInfo(): Promise<PayInfo> {
  return api.get<PayInfo>("/payments/pay-info");
}

/** 下单 */
export async function createOrder(skuCode: string): Promise<PaymentOrder> {
  return api.post<PaymentOrder>("/payments/orders", { sku_code: skuCode });
}

/** 我的订单列表 */
export async function listMyOrders(): Promise<PaymentOrder[]> {
  const r = await api.get<{ items: PaymentOrder[] }>("/payments/orders");
  return r.items ?? [];
}

/** 单个订单详情(轮询审核状态用)*/
export async function getOrder(orderId: string): Promise<PaymentOrder> {
  return api.get<PaymentOrder>(`/payments/orders/${encodeURIComponent(orderId)}`);
}

/** 上传付款截图 */
export async function submitProof(orderId: string, file: File): Promise<PaymentOrder> {
  const form = new FormData();
  form.append("file", file);
  return api.post<PaymentOrder>(
    `/payments/orders/${encodeURIComponent(orderId)}/proof`,
    form,
  );
}
