/**
 * useGraphTheme — 把 CSS variable 中定义的颜色拿到 JS 侧给 Three.js 用
 *
 * Three.js 不能直接读 CSS variable,要在 mount 后用 getComputedStyle 拿。
 * 这个 composable 把所有可能用到的颜色一次性读出,封装成 ThemeColors 对象,
 * 给 CrystalGraph 的 nodeThreeObject / linkColor 等回调使用。
 */

import type { CharacterTone, RelationType } from "../types/graph";

export interface ThemeColors {
  deepSpace: string;
  crystalLight: string;
  crystalDim: string;

  /** 关系类型 → 主色 */
  relation: Record<RelationType, string>;
  /** 关系类型 → 渐变末端色 */
  relationEnd: Record<RelationType, string>;

  /** 角色性格基调 → 主色 */
  characterTone: Record<CharacterTone, string>;
  characterToneEnd: Record<CharacterTone, string>;

  /** 类型 fallback(当 entity 是 LOCATION/OBJECT/EVENT 时用) */
  typeColor: Record<"PERSON" | "LOCATION" | "OBJECT" | "EVENT", string>;
}

/** 每个 RelationType 对应的 CSS 变量名后缀(全部小写、连字符) */
const RELATION_VAR_KEY: Record<RelationType, string> = {
  亲属: "kin",
  敌对: "foe",
  主仆: "master",
  情侣: "lover",
  朋友: "friend",
  同事: "coworker",
  参与: "coworker",       // 参与与同事共用一组色,language 上接近
  师徒: "coworker",       // 师徒也归在协作色相
  位于: "located",
  拥有: "located",
  提及: "mention",
};

const TONE_VAR_KEY: Record<CharacterTone, string> = {
  cold: "cold",
  hot: "hot",
  calm: "calm",
  tender: "tender",
  shadow: "shadow",
  honest: "honest",
};

function readVar(name: string): string {
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return v || "#FFFFFF";
}


// ============================================================
// Sprint 6.A2 M7.H(2026-05-20)— 自定义关系类型 hash 派生色
// ============================================================
//
// 背景:M7.G 关系类型自由化后,LLM 可自创"暗恋""革命战友""忘年交"等关系名,
// 用户也可自定义。这些不在 11 种预设(亲属/敌对/朋友/...) 里,渲染时若直接 fallback
// 灰色会导致大量自定义关系视觉混淆。
//
// 算法:用字符串 hash → HSL 色域,稳定输出(同 type 永远同色)+ 视觉舒适。
//   - Hue: 0-360°(全色相);通过 hash modulo 分散
//   - Saturation: 55%(适度;避免太刺眼)
//   - Lightness: 65%(中性偏亮;深色 deep-space 背景下可见)
//
// 不避开预设色相:11 种预设占 11 个 hue 点,360° 内自然撞色概率 ≈ 11/360 = 3%;
// 即使撞色,自定义关系仍有"区分度",用户能通过 hover tooltip 看到 type 名。

/** 简单稳定字符串 hash(djb2 变种)— 输入任意 string,返回 0..2^31 整数 */
function _hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) + s.charCodeAt(i);
    h = h & 0x7FFFFFFF;    // 保持 31-bit 正整数
  }
  return h;
}

/**
 * 自定义关系类型的稳定派生色。
 *
 * 用于 CrystalGraph.linkColor 在 theme.relation[type] 查无时兜底。
 * 同一 type 字符串永远输出同一颜色(决定性 hash + 固定饱和度 / 亮度)。
 *
 * @returns CSS HSL color string,如 "hsl(192, 55%, 65%)"
 */
export function hashColorForType(type: string): string {
  if (!type || typeof type !== "string") {
    return "hsl(0, 0%, 55%)";    // 兜底中性灰(理论上不会触发,DB CHECK 已防 NULL)
  }
  const h = _hashString(type);
  const hue = h % 360;
  // S=55% L=65%(deep-space 背景下可见,不刺眼)
  return `hsl(${hue}, 55%, 65%)`;
}

export function loadThemeColors(): ThemeColors {
  const relation = {} as Record<RelationType, string>;
  const relationEnd = {} as Record<RelationType, string>;
  const characterTone = {} as Record<CharacterTone, string>;
  const characterToneEnd = {} as Record<CharacterTone, string>;

  (Object.keys(RELATION_VAR_KEY) as RelationType[]).forEach((rt) => {
    const key = RELATION_VAR_KEY[rt];
    relation[rt] = readVar(`--hj-rel-${key}`);
    relationEnd[rt] = readVar(`--hj-rel-${key}-end`);
  });

  (Object.keys(TONE_VAR_KEY) as CharacterTone[]).forEach((tone) => {
    const key = TONE_VAR_KEY[tone];
    characterTone[tone] = readVar(`--hj-char-${key}`);
    characterToneEnd[tone] = readVar(`--hj-char-${key}-end`);
  });

  // 非 PERSON 类型的 fallback 色(LOCATION 暖金、OBJECT 银白、EVENT 紫荧)
  const typeColor = {
    PERSON: characterTone.calm,           // PERSON 没有 tone 时退到 calm 色
    LOCATION: "#D9B26A",                  // 暖金,体现"地方/场所"的稳重
    OBJECT: "#C4D4E8",                    // 银白,体现"器物"的中性
    EVENT: "#B388FF",                     // 紫荧,体现"事件"的张力
  };

  return {
    deepSpace: readVar("--hj-deep-space"),
    crystalLight: readVar("--hj-crystal-light"),
    crystalDim: readVar("--hj-crystal-dim"),
    relation,
    relationEnd,
    characterTone,
    characterToneEnd,
    typeColor,
  };
}
