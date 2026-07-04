<script setup lang="ts">
/**
 * ScreenplayScript — 广场剧本专业排版阅读组件(2026-07-03,修图四"排版像小说")。
 *
 * 输入是「浑晶 · 剧创态」exporter(screenplay_exporter.export_to_txt)产出的中文剧本 TXT。
 * 小说阅读器用比例衬线体渲染它 → 靠空格模拟的居中/右对齐全乱掉 = 图四那种"排版很烂"。
 *
 * 本组件把该 TXT **反解析成语义块**(标题 / 角色表 / 场头 / 概要 / 动作 / 台词 / 潜台词 /
 * 过场),再用真正的剧本排版(场头带序号+分隔线、角色名居中、台词窄栏、过场右对齐)呈现,
 * 主题(浅/护眼/暗)跟随外层阅读器的 --r-* 变量。
 *
 * 解析基于 exporter 的确定性格式,不依赖任何 markdown / 第三方库(架构冻结闸门)。
 */
import { computed, ref } from "vue";

const props = defineProps<{
  content: string;
  fontSize?: number;
}>();

// 根滚动容器 —— 暴露给外层阅读器做键盘滚动(方向键/空格/翻页键)
const rootRef = ref<HTMLElement | null>(null);
function scrollStep(dy: number) {
  rootRef.value?.scrollBy({ top: dy, behavior: "smooth" });
}
function scrollByViewport(fraction: number) {
  const el = rootRef.value;
  if (el) el.scrollBy({ top: el.clientHeight * fraction, behavior: "smooth" });
}
function scrollToEdge(end: boolean) {
  const el = rootRef.value;
  if (el) el.scrollTo({ top: end ? el.scrollHeight : 0, behavior: "smooth" });
}
defineExpose({ scrollStep, scrollByViewport, scrollToEdge });

type Block =
  | { kind: "title"; text: string }
  | { kind: "subtitle"; text: string }
  | { kind: "cast"; items: { name: string; desc: string }[] }
  | { kind: "scene"; no: string; slug: string }
  | { kind: "summary"; text: string }
  | { kind: "action"; text: string }
  | { kind: "dialogue"; character: string; parenthetical: string; text: string }
  | { kind: "paren"; text: string }
  | { kind: "transition"; text: string }
  | { kind: "end" };

const blocks = computed<Block[]>(() => parse(props.content || ""));

/**
 * 反解析 exporter 的 TXT。逐行按缩进 + 标记归类:
 *  - `《x》` 标题 · `改编自 — x` 副标题
 *  - `【角色】` + `  · 名 — 简介` 角色表
 *  - `【SCENE 001】 INT. 地点 — 时段` 场头
 *  - `  [本场概要] …` 概要
 *  - 4 空格缩进 = 台词正文(归属前一条角色提示)
 *  - `(…)` 缩进行 = 潜台词括注(有 pending 归台词,否则独立环境提示)
 *  - 全大写拉丁 + 冒号 + 缩进 = 过场
 *  - 其它缩进行 = 角色提示(开一条待接台词)
 *  - 无缩进 = 动作
 */
function parse(raw: string): Block[] {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let cast: { name: string; desc: string }[] | null = null;
  let pending: { character: string; parenthetical: string } | null = null;
  // 上一条台词块 —— 供多行台词续行追加(exporter 只给首行 4 空格缩进,
  // 后续行落到 0 缩进,会被误判成 action;空行/新块出现即清空)
  let lastDialogue: Extract<Block, { kind: "dialogue" }> | null = null;

  const flushCast = () => {
    if (cast && cast.length) out.push({ kind: "cast", items: cast });
    cast = null;
  };

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    const indent = rawLine.length - rawLine.trimStart().length;

    if (trimmed === "") { lastDialogue = null; continue; }
    if (/^={5,}$/.test(trimmed) || /^-{5,}$/.test(trimmed)) { lastDialogue = null; continue; }

    // 结束 / 页脚
    if (trimmed === "— 剧本结束 —") {
      flushCast();
      pending = null;
      lastDialogue = null;
      out.push({ kind: "end" });
      continue;
    }
    if (trimmed.startsWith("由「浑晶")) continue;

    // 标题页
    const mTitle = trimmed.match(/^《(.+)》$/);
    if (mTitle) {
      flushCast();
      out.push({ kind: "title", text: mTitle[1] });
      continue;
    }
    if (trimmed.startsWith("改编自")) {
      out.push({ kind: "subtitle", text: trimmed.replace(/^改编自\s*[—-]\s*/, "") });
      continue;
    }

    // 角色表
    if (trimmed === "【角色】") {
      cast = [];
      continue;
    }
    if (cast !== null && /^·\s+/.test(trimmed)) {
      const body = trimmed.replace(/^·\s+/, "");
      const parts = body.split(/\s+—\s+/);
      cast.push({ name: parts[0] || body, desc: parts.slice(1).join(" — ") });
      continue;
    }
    if (cast !== null) flushCast(); // 角色表遇到别的块即收尾

    // 场头
    const mScene = trimmed.match(/^【SCENE\s+(\d+)】\s*(.*)$/);
    if (mScene) {
      pending = null;
      lastDialogue = null;
      out.push({ kind: "scene", no: mScene[1], slug: mScene[2].trim() });
      continue;
    }

    // 概要
    if (/^\[本场概要\]/.test(trimmed)) {
      out.push({ kind: "summary", text: trimmed.replace(/^\[本场概要\]\s*/, "") });
      continue;
    }

    // 台词正文(exporter 固定 4 空格缩进,且前面有角色提示)
    if (indent === 4 && pending) {
      const blk: Extract<Block, { kind: "dialogue" }> = {
        kind: "dialogue",
        character: pending.character,
        parenthetical: pending.parenthetical,
        text: trimmed,
      };
      out.push(blk);
      lastDialogue = blk; // 记住,供后续续行追加
      pending = null;
      continue;
    }

    // 括注(缩进行,整行括号)
    if (indent > 0 && /^[（(].*[)）]$/.test(trimmed)) {
      const inner = trimmed.replace(/^[（(]/, "").replace(/[)）]$/, "");
      if (pending) pending.parenthetical = inner;
      else { lastDialogue = null; out.push({ kind: "paren", text: inner }); }
      continue;
    }

    // 过场(缩进 + 大写拉丁/数字/连字符 + 冒号)
    if (indent > 0 && /^[A-Z0-9][A-Z0-9 .\-]*[:：]$/.test(trimmed)) {
      pending = null;
      lastDialogue = null;
      out.push({ kind: "transition", text: trimmed.replace(/[:：]$/, "") });
      continue;
    }

    // 角色提示(其它缩进行)→ 开一条待接台词
    if (indent > 0) {
      lastDialogue = null;
      pending = { character: trimmed, parenthetical: "" };
      continue;
    }

    // 无缩进:多行台词的续行 → 追加到上一条台词;否则是动作
    pending = null;
    if (lastDialogue) {
      lastDialogue.text += "\n" + trimmed;
      continue;
    }
    out.push({ kind: "action", text: trimmed });
  }

  flushCast();
  return out;
}

/** 场景锚点(≥2 场时给右侧快速跳转)*/
const scenes = computed(() =>
  blocks.value
    .map((b, i) => (b.kind === "scene" ? { i, no: b.no, slug: b.slug } : null))
    .filter((x): x is { i: number; no: string; slug: string } => x !== null),
);

function jumpTo(no: string) {
  const el = document.getElementById(`sp-scene-${no}`);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}
</script>

<template>
  <div ref="rootRef" class="sp-script" :style="{ '--sp-fs': `${fontSize || 18}px` }">
    <article class="sp-paper">
      <template v-for="(b, i) in blocks" :key="i">
        <h1 v-if="b.kind === 'title'" class="sp-title">{{ b.text }}</h1>
        <p v-else-if="b.kind === 'subtitle'" class="sp-subtitle">改编自 · {{ b.text }}</p>

        <section v-else-if="b.kind === 'cast'" class="sp-cast">
          <div class="sp-cast-head">主要角色</div>
          <ul>
            <li v-for="(c, ci) in b.items" :key="ci">
              <span class="sp-cast-name">{{ c.name }}</span>
              <span v-if="c.desc" class="sp-cast-desc">{{ c.desc }}</span>
            </li>
          </ul>
        </section>

        <div v-else-if="b.kind === 'scene'" :id="`sp-scene-${b.no}`" class="sp-scene">
          <span class="sp-scene-no">{{ Number(b.no) }}</span>
          <span class="sp-scene-slug">{{ b.slug }}</span>
        </div>

        <p v-else-if="b.kind === 'summary'" class="sp-summary">{{ b.text }}</p>
        <p v-else-if="b.kind === 'action'" class="sp-action">{{ b.text }}</p>

        <div v-else-if="b.kind === 'dialogue'" class="sp-dia">
          <div class="sp-char">{{ b.character }}</div>
          <div v-if="b.parenthetical" class="sp-paren">({{ b.parenthetical }})</div>
          <div class="sp-line">{{ b.text }}</div>
        </div>

        <p v-else-if="b.kind === 'paren'" class="sp-standalone-paren">({{ b.text }})</p>
        <p v-else-if="b.kind === 'transition'" class="sp-transition">{{ b.text }}</p>
        <div v-else-if="b.kind === 'end'" class="sp-end">— 剧终 —</div>
      </template>
    </article>

    <!-- 场景快速跳转(≥2 场,窄屏隐藏)-->
    <nav v-if="scenes.length > 1" class="sp-scene-nav" aria-label="场景跳转">
      <button
        v-for="s in scenes"
        :key="s.no"
        class="sp-scene-nav-item"
        :title="s.slug"
        @click="jumpTo(s.no)"
      >
        {{ Number(s.no) }}
      </button>
    </nav>
  </div>
</template>

<style scoped>
.sp-script {
  position: relative;
  height: 100%;
  overflow-y: auto;
  background: var(--r-bg);
  color: var(--r-text);
}

.sp-paper {
  max-width: 720px;
  margin: 0 auto;
  padding: clamp(24px, 5vw, 60px) clamp(20px, 6vw, 72px) 140px;
  font-size: var(--sp-fs, 18px);
  line-height: 1.78;
}

/* ---- 标题页 ---- */
.sp-title {
  text-align: center;
  font-size: 1.75em;
  font-weight: 800;
  letter-spacing: 0.04em;
  margin: 20px 0 6px;
}
.sp-subtitle {
  text-align: center;
  color: var(--r-muted);
  margin: 0 0 30px;
  font-size: 0.92em;
}

/* ---- 角色表 ---- */
.sp-cast {
  margin: 0 0 34px;
  padding: 16px 20px;
  border: 1px solid var(--r-border);
  border-radius: 12px;
}
.sp-cast-head {
  font-weight: 700;
  letter-spacing: 0.08em;
  font-size: 0.86em;
  margin-bottom: 10px;
  color: var(--r-muted);
}
.sp-cast ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sp-cast-name {
  font-weight: 600;
}
.sp-cast-desc {
  color: var(--r-muted);
  font-size: 0.86em;
  margin-left: 10px;
}

/* ---- 场头 ---- */
.sp-scene {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 46px 0 18px;
  padding-bottom: 8px;
  border-bottom: 1.5px solid var(--r-border);
  font-weight: 700;
  scroll-margin-top: 76px;
}
.sp-scene:first-child {
  margin-top: 6px;
}
.sp-scene-no {
  flex-shrink: 0;
  font-size: 0.7em;
  font-weight: 700;
  color: var(--r-bg);
  background: var(--r-accent);
  padding: 3px 9px;
  border-radius: 5px;
  letter-spacing: 0.05em;
}
.sp-scene-slug {
  text-transform: uppercase;
  font-size: 0.92em;
  letter-spacing: 0.06em;
}

/* ---- 概要(场头下的一句话,弱化)---- */
.sp-summary {
  margin: 0 0 20px;
  padding: 6px 0 6px 12px;
  border-left: 2px solid var(--r-border);
  font-size: 0.82em;
  font-style: italic;
  color: var(--r-muted);
}

/* ---- 动作 ---- */
.sp-action {
  margin: 0 0 15px;
  white-space: pre-line; /* 保留可能的换行 */
}

/* ---- 台词块(整块居中窄栏,台词左对齐)---- */
.sp-dia {
  max-width: 66%;
  margin: 0 auto 18px;
}
.sp-char {
  text-align: center;
  font-weight: 700;
  letter-spacing: 0.14em;
}
.sp-paren {
  text-align: center;
  font-size: 0.83em;
  font-style: italic;
  color: var(--r-muted);
  margin-top: 1px;
}
.sp-line {
  text-align: left;
  margin-top: 5px;
  white-space: pre-line; /* 多行台词保留换行 */
}

/* ---- 独立环境提示 ---- */
.sp-standalone-paren {
  text-align: center;
  font-style: italic;
  color: var(--r-muted);
  margin: 0 0 15px;
}

/* ---- 过场 ---- */
.sp-transition {
  text-align: right;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--r-muted);
  margin: 10px 0 22px;
}

/* ---- 剧终 ---- */
.sp-end {
  text-align: center;
  letter-spacing: 0.3em;
  color: var(--r-muted);
  font-weight: 600;
  margin: 44px 0 8px;
}

/* ---- 右侧场景跳转 ---- */
.sp-scene-nav {
  position: fixed;
  right: 14px;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 6px;
  background: var(--r-chrome-bg);
  border: 1px solid var(--r-border);
  border-radius: 12px;
  backdrop-filter: blur(6px);
  z-index: 20;
}
.sp-scene-nav-item {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--r-muted);
  font-size: 12px;
  cursor: pointer;
  transition: background 120ms, color 120ms;
}
.sp-scene-nav-item:hover {
  background: var(--r-accent);
  color: var(--r-bg);
}

@media (max-width: 640px) {
  .sp-scene-nav {
    display: none;
  }
  .sp-dia {
    max-width: 82%;
  }
}
</style>
