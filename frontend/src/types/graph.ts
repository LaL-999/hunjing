/**
 * 浑晶图谱数据类型 — 与后端 data/graphs/*.json 结构对齐
 *
 * 后端产物路径:
 *   huimeng/data/graphs/ch74.json
 *
 * 这里的字段命名要和那份 JSON 一致(蛇形/驼形保持兼容)。
 */

export type EntityType = "PERSON" | "LOCATION" | "OBJECT" | "EVENT";

/**
 * 关系类型 — Sprint 6.A2 M7.G(2026-05-20)起后端自由化,任意 1-20 字字符串都合法。
 * 此处保留 11 种**预设枚举**作为 useGraphTheme 主色查找的 key;
 * 实际 `Relation.type` / `GraphLink.type` 是宽 string,自定义关系通过 hash 派生色。
 */
export type RelationType =
  | "亲属"
  | "主仆"
  | "朋友"
  | "情侣"
  | "敌对"
  | "同事"
  | "师徒"
  | "参与"
  | "位于"
  | "拥有"
  | "提及";

/** 性格基调,用于映射到色相组(LLM 在 ingest 时赋值) */
export type CharacterTone =
  | "cold"      // 冷峭·青冷
  | "hot"       // 热烈·暖橙
  | "calm"      // 沉静·紫蓝
  | "tender"    // 痴情·粉紫
  | "shadow"    // 阴鸷·暗绿
  | "honest";   // 朴拙·米黄

export interface Entity {
  name: string;
  type: EntityType;
  aliases?: string[];
  description?: string;
  /** 仅 PERSON 类型有,LLM ingest 时根据 personality 自动赋值 */
  tone?: CharacterTone;
}

export interface Relation {
  source: string;
  target: string;
  /** M7.G(2026-05-20):自由 string;预设 11 种由 useGraphTheme 主色查找,
   *  自定义(如"暗恋"/"革命战友"/"忘年交")通过 hashColorForType 派生色 */
  type: string;
  description?: string;
}

export interface GraphPayload {
  source_file: string;
  source_char_count: number;
  n_entities: number;
  n_relations: number;
  entities: Entity[];
  relations: Relation[];
}

/** 3d-force-graph 内部消费的节点结构(扩展自 Entity) */
export interface GraphNode extends Entity {
  id: string;          // 在 force-graph 内必须有 id
  // force-graph 会写入这些位置字段
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
}

export interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  /** M7.G(2026-05-20):自由 string;CrystalGraph linkColor 用 useGraphTheme 兜底 hash */
  type: string;
  description?: string;
  /** Sprint D.2.B fix:关系强度 score 0-100,从 RELATIONSHIP_STRENGTH_SCORE 映射。
   *  把"按 threshold 过滤"从 transformBackendGraph 挪到 CrystalGraph runtime —
   *  force-graph 不再 reload data 重建 line(线条闪烁的根因),threshold prop 变化
   *  只触发 forceUpdateLinkOpacity 改 material.opacity 即可。 */
  strengthScore?: number;
}

export interface ForceGraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}
