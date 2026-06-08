/**
 * Refinement 类型 — 严格对齐 backend/app/schemas/refine.py。
 */

export type SuggestionKind =
  | "identity_补全"
  | "personality_补充"
  | "quote_补充"
  | "no_go_补充"
  | "consistency_警告";

export type RefinementStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "edited"
  | "skipped";

/** payload 三种形态(联合) */
export interface PayloadAppend {
  field: "personality" | "quotes" | "no_go_list";
  append: string | string[];
}

export interface PayloadValue {
  field: "identity";
  value: string;
}

export interface PayloadWarning {
  kind: "warning";
  fields: string[];
  current_a: string;
  current_b: string;
}

export type SuggestionPayload = PayloadAppend | PayloadValue | PayloadWarning;

export interface Refinement {
  id: string;
  character_id: string;
  character_name: string;
  suggestion_kind: SuggestionKind;
  suggestion_text: string;
  suggestion_payload: SuggestionPayload;
  status: RefinementStatus;
}

export interface RefineSessionStats {
  characters_count: number;
  refinements_count: number;
  tokens: { input_tokens: number; output_tokens: number };
  cost_yuan: number;
  duration_ms: number;
  filtered_out: number;
}

export interface RefineProjectResponse {
  session_id: string;
  refinements: Refinement[];
  stats: RefineSessionStats;
}

export interface ActionRefinementRequest {
  action: "accept" | "reject" | "edit";
  user_edit?: SuggestionPayload;
}

export interface ActionRefinementResponse {
  id: string;
  status: RefinementStatus;
  applied_to_character: boolean;
}

export interface SkipSessionRequest {
  reason: "user_skipped" | "llm_failed";
}

export interface SkipSessionResponse {
  skipped: boolean;
  remaining_refinements: number;
}

/** UI 中文 label */
export const KIND_LABEL: Record<SuggestionKind, string> = {
  identity_补全: "补充身份",
  personality_补充: "补充性格",
  quote_补充: "补充原话",
  no_go_补充: "补充雷区",
  consistency_警告: "矛盾提醒",
};
