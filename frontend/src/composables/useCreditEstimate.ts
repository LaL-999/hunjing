/**
 * useCreditEstimate — 前端 credit 消耗预估(Sprint C.3,2026-05-13)。
 *
 * 镜像后端 `credit_service.credit_units_for_*_call` 的单价表,给用户"预计消耗 X c"
 * 透明提示。前端预估**仅供参考**(用户决策用),真实扣费按 LLM 完成后真 token 算。
 *
 * 单价(对齐 docs/ADR_credit_quota_重构.md §2.2):
 *   DeepSeek 1K input token   = 0.013 c
 *   DeepSeek 1K output token  = 0.026 c
 *   Qwen-VL 1K vision token   = 0.26 c
 *   Seedream 4.0 1 张         = 2.6 c
 *
 * 预估场景:
 *   - simulation(continuation):按 reshape_percent → rounds → token
 *   - extract:按上传字数粗估
 *   - refine:固定低成本(对焦 = 单角色单次 ~0.5c)
 *   - comic_create:留 C.4 Planner 给真实预估,此处占位
 */

// ============================================================
// 各场景预估算法
// ============================================================

/**
 * AI 推演预估 — 基于 reshape_percent 派生 rounds_planned。
 *
 * 后端 simulation_service 完整 30 轮的 ADR §2.3 基线是 ~78c;
 * 派生 round 数 = `max(5, round((reshape - 10) / 8) + 5)`(对齐后端)
 * 平均每轮 ≈ 2.6c(78c / 30 轮),实际按真 token 扣。
 */
export function estimateSimulationCredits(
  reshapePercent: number,
  contextSimulationCount: number = 0,
): number {
  const rounds = Math.max(5, Math.round((reshapePercent - 10) / 8) + 5);
  const perRound = 2.6;
  let estimate = rounds * perRound;
  // 滚雪球前文每条加 ~3c(前文注入 context tokens)
  estimate += contextSimulationCount * 3;
  return Math.max(1, Math.round(estimate));
}

/**
 * AI 抽图谱预估 — 按上传字数粗估。
 *
 * 每万字粗估 ~3c(chunk LLM input + entities/relations output)。
 * 完整 50K 字小说 → ~15c。
 */
export function estimateExtractCredits(uploadCharCount: number): number {
  const perWanZi = 3;
  const estimate = Math.max(1, (uploadCharCount / 10000) * perWanZi);
  return Math.round(estimate);
}

/**
 * AI 角色对焦预估 — 单次轻量调用。
 *
 * 单角色一次对焦 ~3K input + 1K output ≈ 0.065c,粗估 1c 给用户兜底直觉。
 */
export function estimateRefineCredits(characterCount: number): number {
  return Math.max(1, Math.ceil(characterCount * 0.5));
}

/**
 * 漫画创建预估 — Sprint C.4 AI Planner 接通前的占位值。
 *
 * 完整 12 页漫画(ADR v3)≈ 222c;
 * 完整 30 页 ≈ 510c;
 * 平均粗估 250c。
 */
export function estimateComicCredits(targetPages: number = 12): number {
  // 每页 ~18.5c(72 格 × 2.6c / 12 页 + 编剧 5c + 素材 3c + 锚定 30c)
  const perPage = 18.5;
  const overhead = 40;
  return Math.max(100, Math.round(targetPages * perPage + overhead));
}

// ============================================================
// 公共预估接口
// ============================================================

export interface CreditEstimate {
  units: number;
  /** 一句给用户看的解释("基于 X 轮 / X 字 / ..." */
  basis: string;
}

export function estimateForAction(
  action: "simulation" | "extract" | "refine" | "comic_create",
  params: {
    reshapePercent?: number;
    contextSimulationCount?: number;
    uploadCharCount?: number;
    characterCount?: number;
    targetPages?: number;
  },
): CreditEstimate {
  switch (action) {
    case "simulation": {
      const reshape = params.reshapePercent ?? 50;
      const ctxN = params.contextSimulationCount ?? 0;
      const units = estimateSimulationCredits(reshape, ctxN);
      const rounds = Math.max(5, Math.round((reshape - 10) / 8) + 5);
      return {
        units,
        basis: ctxN > 0
          ? `${rounds} 轮 + ${ctxN} 段前文承接`
          : `${rounds} 轮推演`,
      };
    }
    case "extract": {
      const chars = params.uploadCharCount ?? 0;
      return {
        units: estimateExtractCredits(chars),
        basis: chars > 0 ? `${(chars / 10000).toFixed(1)} 万字` : "未知字数",
      };
    }
    case "refine": {
      const chars = params.characterCount ?? 1;
      return {
        units: estimateRefineCredits(chars),
        basis: `${chars} 个角色`,
      };
    }
    case "comic_create": {
      const pages = params.targetPages ?? 12;
      return {
        units: estimateComicCredits(pages),
        basis: `${pages} 页漫画`,
      };
    }
  }
}
