<script setup lang="ts">
/**
 * 角色页 + 关系图主面板 — 阶段 8.2(2026-06-08)。
 *
 * 全屏 modal,左侧:关系图 + 概览;右侧:角色卡列表(含桥接资产 chips)。
 * 点击关系图节点 → 高亮对应卡片;点击卡片 → 高亮节点。
 * 标签页:全部 / 主角 / 配角 / 龙套 / 群演。
 */
import { computed, onMounted, ref, watch } from "vue";

import CharacterRelationshipGraph from "./CharacterRelationshipGraph.vue";
import {
  getCharacterProfiles,
  type CharacterProfilesResponse,
  type CharacterProfileApi,
} from "../api/screenplay-client";
import { toast } from "../../composables/useToast";

const props = defineProps<{
  novelId: string;
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const data = ref<CharacterProfilesResponse | null>(null);
const loading = ref(false);
const errorMsg = ref<string>("");

const TIER_LABEL: Record<string, string> = {
  protagonist: "主角",
  supporting: "配角",
  bit_part: "龙套",
  extra: "群演",
};

const TIER_ORDER = ["protagonist", "supporting", "bit_part", "extra"];

const activeTab = ref<string>("all");
const selectedId = ref<string>("");

async function load() {
  loading.value = true;
  errorMsg.value = "";
  try {
    data.value = await getCharacterProfiles(props.novelId);
    // 默认选中第一个主角(若有)
    const protag = data.value.characters.find(c => c.is_protagonist);
    if (protag) selectedId.value = protag.id;
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e);
    toast.error("加载角色档案失败:" + errorMsg.value);
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.visible, props.novelId],
  ([v]) => {
    if (v) load();
  },
);
onMounted(() => {
  if (props.visible) load();
});

const filteredCharacters = computed<CharacterProfileApi[]>(() => {
  if (!data.value) return [];
  const list = data.value.characters;
  if (activeTab.value === "all") {
    // 按戏份层级排序
    return [...list].sort((a, b) => {
      const ta = TIER_ORDER.indexOf(a.stats.role_tier);
      const tb = TIER_ORDER.indexOf(b.stats.role_tier);
      if (ta !== tb) return ta - tb;
      return b.stats.dialogue_count - a.stats.dialogue_count;
    });
  }
  return list.filter(c => c.stats.role_tier === activeTab.value);
});

const tierCounts = computed(() => {
  const counts: Record<string, number> = {
    all: 0, protagonist: 0, supporting: 0, bit_part: 0, extra: 0,
  };
  if (!data.value) return counts;
  counts.all = data.value.characters.length;
  for (const c of data.value.characters) {
    counts[c.stats.role_tier] = (counts[c.stats.role_tier] || 0) + 1;
  }
  return counts;
});

const isLinked = computed(() => !!data.value?.linked_project_id);
const linkedCount = computed(() => {
  if (!data.value) return 0;
  return data.value.characters.filter(c => c.bridge_assets !== null).length;
});

function handleSelectFromGraph(id: string) {
  selectedId.value = id;
  // 滚动到对应卡片
  setTimeout(() => {
    const el = document.getElementById(`char-card-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, 50);
}

function handleClose() {
  emit("close");
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="charprof-overlay" @click.self="handleClose">
      <div class="charprof-panel screenplay-module">
        <!-- 顶栏 -->
        <header class="ph">
          <div class="ph-left">
            <h2 class="literary-heading">角色档案</h2>
            <div class="ph-sub">
              <span v-if="data">{{ data.characters.length }} 个角色</span>
              <span v-if="isLinked" class="bridge-chip">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round"
                     stroke-linejoin="round">
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                </svg>
                已接通浑晶项目 · {{ linkedCount }}/{{ data?.characters.length ?? 0 }} 角色有 SP 资产
              </span>
              <span v-else-if="data" class="unlinked-hint">
                未绑定浑晶项目 — 绑定后这里会显示角色驱动力 / 当前状态等
              </span>
            </div>
          </div>
          <button class="close-btn" @click="handleClose" title="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        <!-- loading / error -->
        <div v-if="loading" class="state-msg">正在聚合角色数据 + 桥接资产…</div>
        <div v-else-if="errorMsg" class="state-msg state-msg--err">{{ errorMsg }}</div>

        <!-- 主体 -->
        <div v-else-if="data" class="body">
          <!-- 左:关系图 -->
          <section class="left">
            <CharacterRelationshipGraph
              :nodes="data.graph.nodes"
              :edges="data.graph.edges"
              :selected-id="selectedId"
              :width="540"
              :height="440"
              @select-character="handleSelectFromGraph"
            />
          </section>

          <!-- 右:角色卡列表 -->
          <section class="right">
            <!-- tier 切换 -->
            <div class="tabs">
              <button
                v-for="t in ['all', 'protagonist', 'supporting', 'bit_part', 'extra']"
                :key="t"
                class="tab"
                :class="{ active: activeTab === t }"
                @click="activeTab = t"
              >
                {{ t === "all" ? "全部" : TIER_LABEL[t] }}
                <span class="tab-count">{{ tierCounts[t] || 0 }}</span>
              </button>
            </div>

            <ul class="char-list">
              <li
                v-for="c in filteredCharacters"
                :key="c.id"
                :id="`char-card-${c.id}`"
                class="char-card"
                :class="{
                  selected: c.id === selectedId,
                  protag: c.is_protagonist,
                  'has-bridge': c.bridge_assets !== null,
                }"
                @click="selectedId = c.id"
              >
                <div class="cc-hdr">
                  <div class="cc-name-row">
                    <h3 class="cc-name literary">{{ c.name }}</h3>
                    <span class="tier-badge" :class="`tier-${c.stats.role_tier}`">
                      {{ TIER_LABEL[c.stats.role_tier] }}
                    </span>
                    <span v-if="c.bridge_assets" class="bridge-mini-chip" title="已接通浑晶资产">
                      ✨
                    </span>
                  </div>
                  <div v-if="c.aka.length > 0" class="cc-aka">
                    别名:{{ c.aka.join(" / ") }}
                  </div>
                </div>

                <p v-if="c.description" class="cc-desc">{{ c.description }}</p>

                <!-- 戏份统计 -->
                <div class="cc-stats">
                  <span class="stat">出场 {{ c.stats.scene_count }} 场</span>
                  <span class="stat-sep">·</span>
                  <span class="stat">{{ c.stats.chapter_count }} 章</span>
                  <span class="stat-sep">·</span>
                  <span class="stat">{{ c.stats.dialogue_count }} 句台词</span>
                  <span v-if="c.stats.voiceover_count > 0" class="stat-sep">·</span>
                  <span v-if="c.stats.voiceover_count > 0" class="stat">
                    {{ c.stats.voiceover_count }} 句 V.O.
                  </span>
                  <span v-if="c.stats.first_appearance_number !== null" class="stat-sep">·</span>
                  <span v-if="c.stats.first_appearance_number !== null" class="stat">
                    首次出场:第 {{ c.stats.first_appearance_number }} 场
                  </span>
                </div>

                <!-- 桥接资产 chips(已 link 才显示) -->
                <div v-if="c.bridge_assets" class="cc-bridge">
                  <div class="bridge-title">
                    <span>✨ 浑晶 SP 资产</span>
                  </div>
                  <ul class="bridge-list">
                    <li v-if="c.bridge_assets.surface_goal" class="bridge-row">
                      <span class="bridge-label">表层想要</span>
                      <span class="bridge-value">{{ c.bridge_assets.surface_goal }}</span>
                    </li>
                    <li v-if="c.bridge_assets.deep_need" class="bridge-row">
                      <span class="bridge-label">深层需要</span>
                      <span class="bridge-value">{{ c.bridge_assets.deep_need }}</span>
                    </li>
                    <li v-if="c.bridge_assets.fatal_blind_spot" class="bridge-row">
                      <span class="bridge-label">致命盲区</span>
                      <span class="bridge-value">{{ c.bridge_assets.fatal_blind_spot }}</span>
                    </li>
                    <li v-if="c.bridge_assets.arc_from_to" class="bridge-row">
                      <span class="bridge-label">预期弧光</span>
                      <span class="bridge-value">{{ c.bridge_assets.arc_from_to }}</span>
                    </li>
                    <li v-for="(s, i) in c.bridge_assets.secrets" :key="i" class="bridge-row bridge-secret">
                      <span class="bridge-label">秘密</span>
                      <span class="bridge-value">
                        {{ s.description }}
                        <span v-if="s.hidden_from && s.hidden_from.length > 0" class="hidden-from">
                          (对 {{ s.hidden_from.join("、") }} 瞒着)
                        </span>
                      </span>
                    </li>
                    <li v-if="c.bridge_assets.snapshot_position" class="bridge-row">
                      <span class="bridge-label">当前位置</span>
                      <span class="bridge-value">{{ c.bridge_assets.snapshot_position }}</span>
                    </li>
                    <li v-if="c.bridge_assets.snapshot_emotion_top" class="bridge-row">
                      <span class="bridge-label">主导情绪</span>
                      <span class="bridge-value">{{ c.bridge_assets.snapshot_emotion_top }}</span>
                    </li>
                    <li v-if="c.bridge_assets.snapshot_hp_status" class="bridge-row">
                      <span class="bridge-label">身体状态</span>
                      <span class="bridge-value">{{ c.bridge_assets.snapshot_hp_status }}</span>
                    </li>
                    <li v-if="c.bridge_assets.snapshot_inventory.length > 0" class="bridge-row">
                      <span class="bridge-label">随身物品</span>
                      <span class="bridge-value">{{ c.bridge_assets.snapshot_inventory.join("、") }}</span>
                    </li>
                  </ul>
                </div>

                <!-- 关系 -->
                <div v-if="c.relationships.length > 0" class="cc-rels">
                  <div class="rels-title">关系</div>
                  <ul class="rels-list">
                    <li v-for="(r, i) in c.relationships" :key="i" class="rel-row">
                      <span class="rel-arrow">→</span>
                      <span class="rel-target">{{ r.target_name }}</span>
                      <span class="rel-type">{{ r.type }}</span>
                      <span v-if="r.description" class="rel-desc">— {{ r.description }}</span>
                    </li>
                  </ul>
                </div>

                <!-- 关键事件(从 bible_events 派生) -->
                <div v-if="c.key_events.length > 0" class="cc-events">
                  <div class="events-title">参与事件</div>
                  <ul class="events-list">
                    <li v-for="(e, i) in c.key_events" :key="i" class="event-row">
                      <span v-if="e.chapter_number !== null" class="event-ch">
                        第 {{ e.chapter_number }} 章
                      </span>
                      <span class="event-desc">{{ e.description }}</span>
                    </li>
                  </ul>
                </div>
              </li>

              <li v-if="filteredCharacters.length === 0" class="empty">
                此分类暂无角色
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.charprof-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.55);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.charprof-panel {
  background: var(--bg);
  border-radius: var(--radius-lg);
  width: 96vw;
  max-width: 1400px;
  height: 92vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  color: var(--text);
  overflow: hidden;
}

/* 顶栏 */
.ph {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px;
  border-bottom: 1px solid var(--border-soft);
}
.ph-left h2 {
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 500;
  color: var(--text-strong);
}
.ph-sub {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.bridge-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  background: var(--accent-soft);
  color: var(--accent-text);
  border-radius: var(--radius-sm);
  font-weight: 500;
  letter-spacing: 0.04em;
}
.unlinked-hint {
  font-style: italic;
  color: var(--text-muted);
}
.close-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  width: 32px;
  height: 32px;
  cursor: pointer;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 150ms;
}
.close-btn:hover {
  color: var(--text);
  background: var(--hover-bg);
}

/* state msg */
.state-msg {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 14px;
  font-style: italic;
}
.state-msg--err {
  color: var(--danger);
}

/* body */
.body {
  flex: 1;
  display: grid;
  grid-template-columns: 580px 1fr;
  gap: 16px;
  padding: 16px 24px 24px;
  overflow: hidden;
}
@media (max-width: 1080px) {
  .body {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    overflow-y: auto;
  }
}

/* 左:关系图 */
.left {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

/* 右:卡片列表 */
.right {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.tabs {
  display: flex;
  gap: 4px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 12px;
  flex-shrink: 0;
}
.tab {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 5px 12px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 150ms;
}
.tab:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.tab.active {
  background: var(--accent-soft);
  color: var(--accent-text);
  border-color: var(--accent);
}
.tab-count {
  font-family: var(--font-mono);
  font-size: 10.5px;
  opacity: 0.7;
}

.char-list {
  list-style: none;
  margin: 0;
  padding: 0 4px 0 0;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.char-card {
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  padding: 14px 16px;
  background: var(--card-bg);
  cursor: pointer;
  transition: all 200ms;
}
.char-card:hover {
  border-color: var(--accent);
}
.char-card.selected {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.char-card.protag {
  border-left: 3px solid var(--accent);
}
.char-card.has-bridge {
  /* 已接通桥接 — 右上角细微的双层 border 用 box-shadow 实现 */
  box-shadow: inset 0 0 0 1px var(--accent-soft);
}

.cc-hdr {
  margin-bottom: 8px;
}
.cc-name-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.cc-name {
  font-size: 18px;
  margin: 0;
  font-weight: 500;
  color: var(--text-strong);
}
.tier-badge {
  font-size: 10.5px;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.tier-protagonist {
  color: var(--accent-text);
  background: var(--accent-soft);
}
.tier-supporting {
  color: #5d8aa8;
  background: rgba(93, 138, 168, 0.1);
}
.tier-bit_part {
  color: #8a8479;
  background: rgba(138, 132, 121, 0.12);
}
.tier-extra {
  color: var(--text-muted);
  background: var(--code-bg);
}
.bridge-mini-chip {
  font-size: 14px;
}
.cc-aka {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 3px;
}
.cc-desc {
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.6;
  margin: 0 0 8px;
}
.cc-stats {
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  font-family: var(--font-sans);
}
.stat-sep {
  color: var(--border);
}

/* 桥接资产 */
.cc-bridge {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--accent-soft);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--accent);
}
.bridge-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-text);
  letter-spacing: 0.08em;
  margin-bottom: 6px;
}
.bridge-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bridge-row {
  font-size: 11.5px;
  display: flex;
  gap: 8px;
  line-height: 1.5;
}
.bridge-label {
  flex-shrink: 0;
  color: var(--text-muted);
  width: 70px;
}
.bridge-value {
  color: var(--text);
  flex: 1;
}
.bridge-secret .bridge-label {
  color: var(--danger);
}
.hidden-from {
  color: var(--danger);
  font-size: 10.5px;
}

/* 关系 / 事件 */
.cc-rels,
.cc-events {
  margin-top: 10px;
}
.rels-title,
.events-title {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  margin-bottom: 4px;
}
.rels-list,
.events-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rel-row,
.event-row {
  font-size: 11.5px;
  color: var(--text);
  display: flex;
  gap: 6px;
  align-items: baseline;
}
.rel-arrow {
  color: var(--text-muted);
}
.rel-target {
  font-weight: 500;
}
.rel-type {
  font-size: 10.5px;
  padding: 0 6px;
  background: var(--code-bg);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
}
.rel-desc {
  color: var(--text-muted);
  font-size: 11px;
}
.event-ch {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-muted);
  padding: 0 6px;
  background: var(--code-bg);
  border-radius: var(--radius-sm);
}

.empty {
  text-align: center;
  color: var(--text-muted);
  font-style: italic;
  font-size: 13px;
  padding: 32px 0;
}
</style>
