<script setup lang="ts">
/**
 * 桥接增益面板 — 阶段 8.3 第 4 张牌(2026-06-08)。
 *
 * 显示本次 compose 用了多少父平台 SP 资产:
 *   - SP-2 driver 注入次数(每场)
 *   - SP-3 knowledge 注入次数(每场)
 *   - SP-7 polarity 注入次数(splitter + extractor)
 *   - SP-3 facts(项目级,bool)
 *
 * UI:
 *   - 已绑定 + 有注入 → 紫色 banner 展示数字(差异化卖点显形)
 *   - 已绑定 + 0 注入 → 灰色 banner 提示"绑定了但父平台未填字段"
 *   - 未绑定 → 透明小贴士"绑定项目后这里会显示桥接用量"
 */
import { computed } from "vue";
import { useScreenplayStore } from "../stores/screenplay";

const store = useScreenplayStore();

interface BridgeStats {
  bridge_drivers_injections: number;
  bridge_knowledge_injections: number;
  bridge_polarity_injections: number;
  bridge_facts_used: boolean;
  bridge_was_linked: boolean;
}

const bridgeStats = computed<BridgeStats | null>(() => {
  const s = store.stats;
  if (!s || typeof s !== "object") return null;
  // 只在 stats 含 bridge_* 字段时显示(老版本 compose 没这个数据)
  if (!("bridge_was_linked" in s)) return null;
  return {
    bridge_drivers_injections: Number(s.bridge_drivers_injections ?? 0),
    bridge_knowledge_injections: Number(s.bridge_knowledge_injections ?? 0),
    bridge_polarity_injections: Number(s.bridge_polarity_injections ?? 0),
    bridge_facts_used: Boolean(s.bridge_facts_used),
    bridge_was_linked: Boolean(s.bridge_was_linked),
  };
});

const totalInjections = computed(() => {
  if (!bridgeStats.value) return 0;
  return (
    bridgeStats.value.bridge_drivers_injections +
    bridgeStats.value.bridge_knowledge_injections +
    bridgeStats.value.bridge_polarity_injections +
    (bridgeStats.value.bridge_facts_used ? 1 : 0)
  );
});

const mode = computed<"active" | "linked-empty" | "unlinked" | "hidden">(() => {
  if (!bridgeStats.value) return "hidden";
  if (totalInjections.value > 0) return "active";
  if (bridgeStats.value.bridge_was_linked) return "linked-empty";
  return "unlinked";
});
</script>

<template>
  <div v-if="mode !== 'hidden'" class="bgb screenplay-module" :class="`bgb-${mode}`">
    <!-- ACTIVE:有注入,主推差异化卖点 -->
    <template v-if="mode === 'active' && bridgeStats">
      <div class="bgb-icon">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
        </svg>
      </div>
      <div class="bgb-body">
        <div class="bgb-title">
          <span class="bgb-title-text">浑晶桥接已生效</span>
          <span class="bgb-total">本次 compose 注入 {{ totalInjections }} 处父平台资产</span>
        </div>
        <div class="bgb-chips">
          <span
            v-if="bridgeStats.bridge_drivers_injections > 0"
            class="bgb-chip"
            title="SP-2:把每场在场角色的 surface_goal / deep_need / fatal_blind_spot / secrets 注入到 LLM"
          >
            <span class="chip-label">SP-2 角色驱动力</span>
            <span class="chip-num">{{ bridgeStats.bridge_drivers_injections }} 场</span>
          </span>
          <span
            v-if="bridgeStats.bridge_knowledge_injections > 0"
            class="bgb-chip"
            title="SP-3:每场注入「角色已知 vs 未知」清单,防止角色说他不知道的事"
          >
            <span class="chip-label">SP-3 知识边界</span>
            <span class="chip-num">{{ bridgeStats.bridge_knowledge_injections }} 场</span>
          </span>
          <span
            v-if="bridgeStats.bridge_polarity_injections > 0"
            class="bgb-chip"
            title="SP-7:把角色关系正负极注入 scene_splitter 切场 + element_extractor 互称语气"
          >
            <span class="chip-label">SP-7 关系正负极</span>
            <span class="chip-num">{{ bridgeStats.bridge_polarity_injections }} 次</span>
          </span>
          <span
            v-if="bridgeStats.bridge_facts_used"
            class="bgb-chip"
            title="SP-3:项目级故事事实,给改编决策做原作锚定(symbolism 道具不许凭空发明)"
          >
            <span class="chip-label">SP-3 故事事实</span>
            <span class="chip-num">已应用</span>
          </span>
        </div>
      </div>
    </template>

    <!-- LINKED but no injection:已绑定但父平台字段为空 -->
    <template v-else-if="mode === 'linked-empty'">
      <div class="bgb-icon bgb-icon-warn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      </div>
      <div class="bgb-body">
        <div class="bgb-title">
          <span class="bgb-title-text">已绑定项目 — 但父平台暂无可用资产</span>
        </div>
        <p class="bgb-note">
          去浑晶为绑定的项目填角色驱动力(SP-2)/ 知识边界(SP-3)/ 关系正负极(SP-7)
          后,下次 compose 这里会显示具体注入数字。
        </p>
      </div>
    </template>

    <!-- UNLINKED:贴士 -->
    <template v-else-if="mode === 'unlinked'">
      <div class="bgb-icon bgb-icon-muted">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
      </div>
      <div class="bgb-body">
        <p class="bgb-note bgb-note-tip">
          未绑定浑晶项目 — 绑定后这里会显示桥接资产注入次数。
          回主页 → 点小说卡的 🔗 图标可绑定。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bgb {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  margin: 0;
  border: 1px solid transparent;
}

/* active:紫色,主推 */
.bgb-active {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.bgb-active .bgb-icon {
  color: var(--accent);
}
.bgb-active .bgb-title-text {
  color: var(--accent-text);
  font-weight: 600;
}

/* linked-empty:警告色 */
.bgb-linked-empty {
  background: var(--warning-soft);
  border-color: var(--warning);
}
.bgb-icon-warn {
  color: var(--warning);
}

/* unlinked:透明贴士 */
.bgb-unlinked {
  background: var(--code-bg);
  border-color: var(--border-soft);
}
.bgb-icon-muted {
  color: var(--text-muted);
}

.bgb-icon {
  flex-shrink: 0;
  margin-top: 2px;
}
.bgb-body {
  flex: 1;
  min-width: 0;
}
.bgb-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.bgb-title-text {
  font-size: 12.5px;
}
.bgb-total {
  font-size: 11.5px;
  color: var(--text-muted);
}
.bgb-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.bgb-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  background: var(--card-bg);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  font-size: 10.5px;
  letter-spacing: 0.04em;
}
.chip-label {
  color: var(--text);
}
.chip-num {
  font-family: var(--font-mono);
  color: var(--accent-text);
  font-weight: 600;
  font-size: 10px;
}

.bgb-note {
  font-size: 11.5px;
  color: var(--text);
  line-height: 1.5;
  margin: 0;
}
.bgb-note-tip {
  color: var(--text-muted);
  font-style: italic;
}
</style>
