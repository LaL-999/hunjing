<script setup lang="ts">
/**
 * CharacterFocusModal — 角色对焦灵魂模态(对应 prompts/character_focus.md v3)。
 *
 * 4 屏(根据 useRefineSession.phase):
 *   loading    AI 在审视(5-15s spinner)
 *   reviewing  逐条审阅卡片(✓ 采纳 / ✎ 改一改 / ✕ 不采纳 / 跳过剩余)
 *   done       "这是你心中的《XXX》。它和别人心中的不一样。"
 *   error      AI 失败 / 角色不足 / 配额超限
 *
 * Props:
 *   open         父组件控制开关
 *   projectId    当前项目 id
 *   projectName  当前项目名(done 屏展示)
 *
 * Emits:
 *   close(refinedCharacterIds)  用户关闭/完成,带改了的 character_id 给父组件 reload
 *   quota-exceeded(detail)      配额超限,父组件应弹 UpgradeModal
 *
 * 关键 UX:
 *   - reviewing 中不允许 Esc/× 关闭(防丢进度,只能"跳过剩余"显式触发)
 *   - "改一改"切换卡片 inline edit 子状态(textarea + 保存)
 *   - warning 类型只显示 ✕(矛盾提醒不能直接 accept,引导用户回去手改)
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import { toast } from "../composables/useToast";
import { ApiError } from "../api/types";
import type {
  InsufficientCreditsDetail,
  QuotaExceededDetail,
  RefinementItem,
  SuggestionPayload,
  SuggestionPayloadAppend,
  SuggestionPayloadValue,
  SuggestionPayloadWarning,
} from "../api/types";
import { useRefineSession } from "../composables/useRefineSession";

const props = defineProps<{
  open: boolean;
  projectId: string;
  projectName: string;
  /**
   * 1.M.2:从 3D 图谱节点 drawer 触发对焦时,锁定单个角色。
   * 后端仍跑全项目 refine(LLM 需要全上下文做关系敏感建议),前端过滤
   * session.refinements,只展示该角色的建议;无建议则直接进 done 屏。
   * null / undefined → 默认全项目对焦(主页 ProjectView 触发的老路径)
   */
  focusCharacterId?: string | null;
  focusCharacterName?: string | null;
}>();

const emit = defineEmits<{
  (e: "close", refinedCharacterIds: string[]): void;
  (
    e: "quota-exceeded",
    detail: QuotaExceededDetail | InsufficientCreditsDetail,
  ): void;
}>();

const session = useRefineSession(() => props.projectId);

// ============================================================
// 进入 / 退出
// ============================================================

watch(
  () => props.open,
  async (isOpen) => {
    if (isOpen) {
      session.reset();
      cancelInlineEdit();
      await session.start();
      // Sprint C.1:credit 不足 / 资源容量超限 → 立刻通知父组件弹升级 + 关 modal
      if (
        (session.errorCode.value === "QUOTA_EXCEEDED" ||
          session.errorCode.value === "INSUFFICIENT_CREDITS") &&
        session.errorDetail.value
      ) {
        emit(
          "quota-exceeded",
          session.errorDetail.value as
            | QuotaExceededDetail
            | InsufficientCreditsDetail,
        );
        emit("close", []);
        return;
      }
      // 1.M.2:focus 模式 — 客户端过滤 refinements,只留对焦角色的
      if (
        props.focusCharacterId &&
        session.phase.value === "reviewing"
      ) {
        const filtered = session.refinements.value.filter(
          (r) => r.character_id === props.focusCharacterId,
        );
        session.refinements.value = filtered;
        session.currentIndex.value = 0;
        if (filtered.length === 0) {
          // 该角色 LLM 没出建议(其他角色更需要)→ 落 done 屏
          session.phase.value = "done";
        }
      }
    } else {
      session.reset();
      cancelInlineEdit();
    }
  },
);

// ============================================================
// 关闭策略
// ============================================================

// Sprint 6.A2 M1++ bug fix(2026-05-18)+ 修补(2026-05-23):
//   背景点击 / Esc 仍禁(loading 中 LLM 在跑,reviewing 中怕丢进度)— 防误触
//   但 × 是显式取消按钮 — 任何可见阶段都允许关:
//     - loading 中点 × = "中途退出对焦":emit close → 父组件 watch(open) else 分支
//       调 session.reset() → reset 内 _startSeq++ 让 in-flight POST 静默丢弃
//     - reviewing 中 × 仍 v-if 隐藏(模板里),用「跳过剩余」link 显式触发
//     - done / error 阶段 × 直接关
function _disallowImplicitClose(): boolean {
  const phase = session.phase.value;
  return phase === "loading" || phase === "reviewing";
}

function handleCloseRequest() {
  // × 显式点击 — 不走 _disallowImplicitClose 守卫(那是给背景 / Esc 用的)。
  // loading 中关闭后,in-flight 的 session.start() 由 useRefineSession 的 seq token 静默丢弃。
  emit("close", session.refinedCharacterIds.value);
}

function handleEsc() {
  if (_disallowImplicitClose()) return;
  emit("close", session.refinedCharacterIds.value);
}

function handleBackdrop(e: MouseEvent) {
  if (e.target !== e.currentTarget) return;
  if (_disallowImplicitClose()) return;
  emit("close", session.refinedCharacterIds.value);
}

function handleFinish() {
  emit("close", session.refinedCharacterIds.value);
}

async function handleRetry() {
  await session.start();
}

// ============================================================
// payload 类型判断 + 预览(让用户看到"采纳后会写什么")
// ============================================================

function isAppend(p: SuggestionPayload): p is SuggestionPayloadAppend {
  return "append" in p;
}
function isValue(p: SuggestionPayload): p is SuggestionPayloadValue {
  return "value" in p && "field" in p;
}
function isWarning(p: SuggestionPayload): p is SuggestionPayloadWarning {
  return "kind" in p && p.kind === "warning";
}
/** Sprint 6.A2 M1:evolution_hint 类型判定 */
function isEvolutionHint(
  p: SuggestionPayload,
): p is import("../api/types").SuggestionPayloadEvolutionHint {
  return "kind" in p && p.kind === "evolution_hint";
}

const KIND_LABEL: Record<string, string> = {
  identity_补全: "身份",
  personality_补充: "性格",
  quote_补充: "台词",
  no_go_补充: "雷区",
  consistency_警告: "矛盾",
  evolution_hint: "关系演化",       // Sprint 6.A2 M1
  behavior_baseline_补充: "行为基线", // Sprint 6.A2 FOCUS(2026-05-21):4 子字段补全
};

const isWarningType = computed(() => {
  const r = session.currentRefinement.value;
  return r ? isWarning(r.suggestion_payload) : false;
});

/** Sprint 6.A2 M1:evolution_hint 是单独类型,不是 warning */
const isEvolutionHintType = computed(() => {
  const r = session.currentRefinement.value;
  return r ? isEvolutionHint(r.suggestion_payload) : false;
});

const previewText = computed<string | null>(() => {
  const r = session.currentRefinement.value;
  if (!r) return null;
  const p = r.suggestion_payload;
  if (isEvolutionHint(p)) {
    return (
      `早期关系:${p.earlier_type}\n` +
      `当前关系:${p.current_type}\n` +
      `依据:「${p.evidence}」`
    );
  }
  if (isWarning(p)) {
    return `字段 A:${p.current_a ?? ""}\n字段 B:${p.current_b ?? ""}`;
  }
  // C-1 tsc 修复(2026-05-23):p.value 是 string|number(emotional_intensity 数字 / 其他字符串);
  // 这里 computed 声明返 string|null,需显式 String() 强转
  if (isValue(p)) return String(p.value);
  if (isAppend(p)) {
    if (Array.isArray(p.append))
      return p.append.map((x) => `· ${x}`).join("\n");
    return p.append;
  }
  return null;
});

// ============================================================
// Sprint 6.A2 M1:evolution_hint 3 按钮处理
// ============================================================

const evolutionInFlight = ref(false);

/**
 * 选项 1:把 earlier_type 加为 phase[0],当前关系类型留作 phase[1]
 * 实际工程实现:
 *   - 后端 phases API 自动迁移老数据 → phase[0] 复制现有 relationships.type
 *   - 我们再 add_phase(earlier_type, auto_set_current=false)→ 但这会变成 phase[1] 而不是 phase[0]
 *   - 简化策略:把 earlier_type 加为新 phase(在现有 phase[0] 之后)+ 不切 current
 *     用户后续可手动用"设为当前"切回早期阶段(用于反事实 what-if)
 * → 不完美但够用 — M1 阶段允许用户手动整理顺序
 */
async function handleEvolutionAddPhase() {
  const r = session.currentRefinement.value;
  if (!r || !isEvolutionHint(r.suggestion_payload)) return;
  const p = r.suggestion_payload;

  evolutionInFlight.value = true;
  try {
    await api.post(`/relationships/${p.relationship_id}/phases`, {
      type: p.earlier_type,
      strength: "moderate",
      start_anchor: null,
      end_anchor: null,
      notes: `来自 identity 暗示:${p.evidence}`,
      auto_set_current: false,   // 不切 current,保留当前 type
    });
    toast.success(
      `已加 "${p.earlier_type}" 阶段 — 在主项目页"🎭 主角 agent" 卡片展开,可看/调整时间轴`,
      6000,
    );
    await session.action("accept");
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : "加阶段失败";
    toast.error(msg);
  } finally {
    evolutionInFlight.value = false;
  }
}

/** 选项 2:替换当前关系类型为 earlier_type */
async function handleEvolutionReplaceType() {
  const r = session.currentRefinement.value;
  if (!r || !isEvolutionHint(r.suggestion_payload)) return;
  const p = r.suggestion_payload;

  evolutionInFlight.value = true;
  try {
    await api.patch(`/relationships/${p.relationship_id}`, {
      type: p.earlier_type,
    });
    toast.success(`已替换关系类型:${p.current_type} → ${p.earlier_type}`);
    await session.action("accept");
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : "替换失败";
    toast.error(msg);
  } finally {
    evolutionInFlight.value = false;
  }
}

/** 选项 3:这是真矛盾,用户去手动改 → 走原 reject 路径 */
function handleEvolutionRejectAndFix() {
  void session.action("reject");
}

// ============================================================
// inline edit 子状态(✎ 改一改)
// ============================================================

const isEditing = ref(false);
const editValue = ref("");
const editIsList = ref(false);

function startInlineEdit(r: RefinementItem) {
  if (isWarning(r.suggestion_payload)) return;
  const p = r.suggestion_payload;
  if (isValue(p)) {
    // C-1 tsc 修复(2026-05-23):p.value 是 string|number,editValue 是 ref<string>;
    // JS 弱类型本来 work,但 TS 严格,显式 String() 强转
    editValue.value = String(p.value);
    editIsList.value = false;
  } else if (isAppend(p)) {
    if (Array.isArray(p.append)) {
      editValue.value = p.append.join("\n");
      editIsList.value = true;
    } else {
      editValue.value = p.append;
      editIsList.value = false;
    }
  }
  isEditing.value = true;
}

function cancelInlineEdit() {
  isEditing.value = false;
  editValue.value = "";
  editIsList.value = false;
}

async function commitInlineEdit() {
  const r = session.currentRefinement.value;
  if (!r) return;
  const p = r.suggestion_payload;
  const trimmed = editValue.value.trim();
  if (!trimmed) {
    cancelInlineEdit();
    return;
  }

  let userEdit: SuggestionPayload;
  if (isValue(p)) {
    userEdit = { field: p.field, value: trimmed };
  } else if (isAppend(p)) {
    if (editIsList.value) {
      const items = trimmed
        .split("\n")
        .map((x) => x.trim())
        .filter((x) => x.length > 0);
      if (items.length === 0) {
        cancelInlineEdit();
        return;
      }
      userEdit = { field: p.field, append: items };
    } else {
      userEdit = { field: p.field, append: trimmed };
    }
  } else {
    cancelInlineEdit();
    return;
  }

  cancelInlineEdit();
  await session.action("edit", userEdit);
}

const editHint = computed(() =>
  editIsList.value ? "每行一项(回车换行)" : "改成你想要的文本",
);
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="AI 角色对焦"
        @click="handleBackdrop"
        @keydown.esc="handleEsc"
      >
        <div class="modal-card surface">
          <button
            v-if="session.phase.value !== 'reviewing'"
            class="close-btn"
            type="button"
            aria-label="关闭"
            @click="handleCloseRequest"
          >×</button>

          <!-- ========== loading ========== -->
          <div v-if="session.phase.value === 'loading'" class="state-loading">
            <div class="spinner" aria-hidden="true">
              <span class="spinner-dot" />
              <span class="spinner-dot" />
              <span class="spinner-dot" />
            </div>
            <p class="state-title">
              <template v-if="focusCharacterName">AI 正在审视【{{ focusCharacterName }}】</template>
              <template v-else>AI 正在审视你创建的角色</template>
            </p>
            <p class="state-hint">通常 5–15 秒。它会基于你已填的内容做合理推演,不虚构。</p>
          </div>

          <!-- ========== error ========== -->
          <div v-else-if="session.phase.value === 'error'" class="state-error">
            <p class="state-title state-title--err">{{ session.errorMessage.value }}</p>
            <p
              v-if="session.errorCode.value === 'TOO_FEW_CHARACTERS'"
              class="state-hint"
            >先回去创建至少 3 个角色,再来对焦</p>
            <p
              v-else-if="session.errorCode.value === 'LLM_UNAVAILABLE' || session.errorCode.value === 'LLM_OUTPUT_INVALID'"
              class="state-hint"
            >AI 累了,你创建的角色已经够好,可以直接回去</p>
            <div class="state-actions">
              <button
                v-if="session.errorCode.value === 'LLM_UNAVAILABLE' || session.errorCode.value === 'LLM_OUTPUT_INVALID'"
                class="ghost-btn"
                @click="handleRetry"
              >重试</button>
              <button class="primary-btn" @click="handleFinish">关闭</button>
            </div>
          </div>

          <!-- ========== reviewing ========== -->
          <template v-else-if="session.phase.value === 'reviewing' && session.currentRefinement.value">
            <header class="review-header">
              <div class="progress">
                <div
                  class="progress-bar"
                  :style="{ width: `${(session.currentIndex.value / session.totalCount.value) * 100}%` }"
                />
              </div>
              <div class="progress-meta">
                <span class="progress-count mono">
                  {{ session.currentIndex.value + 1 }} / {{ session.totalCount.value }}
                </span>
                <button
                  v-if="session.totalCount.value - session.currentIndex.value > 1"
                  class="link-btn"
                  @click="session.skipRemaining()"
                >跳过剩余</button>
              </div>
            </header>

            <section class="suggestion-card">
              <div class="suggestion-meta">
                <span class="char-name">{{ session.currentRefinement.value.character_name }}</span>
                <span
                  class="kind-chip"
                  :class="{ 'kind-chip--warn': isWarningType }"
                >{{ KIND_LABEL[session.currentRefinement.value.suggestion_kind] }}</span>
              </div>

              <p class="suggestion-text">{{ session.currentRefinement.value.suggestion_text }}</p>

              <pre v-if="previewText && !isEditing" class="payload-preview">{{ previewText }}</pre>

              <p v-if="isWarningType && !isEditing" class="warn-hint">
                这条是矛盾提醒,采纳不会自动修改 — 请回到角色卡手动调整哪一边
              </p>
              <p v-if="isEvolutionHintType && !isEditing" class="evolution-hint">
                <strong>💡 关系演化提示</strong>:这俩字段不是矛盾,而是不同时间点的状态。
                选择下方按钮决定怎么处理:
              </p>

              <!-- inline edit 子态 -->
              <div v-if="isEditing" class="edit-pane">
                <label class="edit-label">改一改 — {{ editHint }}</label>
                <textarea
                  v-model="editValue"
                  class="edit-textarea"
                  :rows="editIsList ? 4 : 3"
                  autofocus
                />
                <div class="edit-actions">
                  <button
                    type="button"
                    class="ghost-btn"
                    @click="cancelInlineEdit"
                  >取消</button>
                  <button
                    type="button"
                    class="primary-btn"
                    :disabled="!editValue.trim() || session.actionInFlight.value"
                    @click="commitInlineEdit"
                  >{{ session.actionInFlight.value ? "保存中…" : "保存这条修改" }}</button>
                </div>
              </div>
            </section>

            <!-- 操作按钮(非 edit 态) -->
            <!-- Sprint 6.A2 M1:evolution_hint 走 3 按钮专属路径 -->
            <div v-if="!isEditing && isEvolutionHintType" class="action-bar action-bar--evolution">
              <button
                class="action-btn action-btn--evolution-add"
                :disabled="evolutionInFlight || session.actionInFlight.value"
                title="把早期类型加为新阶段 phase,保留当前类型(后续可手动调顺序)"
                @click="handleEvolutionAddPhase"
              >
                <span class="action-icon">🕒</span>
                <span>{{ evolutionInFlight ? '处理中…' : '加 phase 保留演化' }}</span>
              </button>
              <button
                class="action-btn action-btn--edit"
                :disabled="evolutionInFlight || session.actionInFlight.value"
                title="把当前关系类型替换为 identity 暗示的早期类型"
                @click="handleEvolutionReplaceType"
              >
                <span class="action-icon">↺</span>
                <span>替换为早期类型</span>
              </button>
              <button
                class="action-btn action-btn--reject"
                :disabled="evolutionInFlight || session.actionInFlight.value"
                title="这是真矛盾,我去手动改"
                @click="handleEvolutionRejectAndFix"
              >
                <span class="action-icon">✕</span>
                <span>是真矛盾,我去改</span>
              </button>
            </div>

            <div v-else-if="!isEditing" class="action-bar">
              <button
                class="action-btn action-btn--reject"
                :disabled="session.actionInFlight.value"
                @click="session.action('reject')"
              >
                <span class="action-icon">✕</span>
                <span>{{ isWarningType ? '我知道了,我去改' : '不采纳' }}</span>
              </button>

              <button
                v-if="!isWarningType"
                class="action-btn action-btn--edit"
                :disabled="session.actionInFlight.value"
                @click="startInlineEdit(session.currentRefinement.value)"
              >
                <span class="action-icon">✎</span>
                <span>改一改</span>
              </button>

              <button
                v-if="!isWarningType"
                class="action-btn action-btn--accept"
                :disabled="session.actionInFlight.value"
                @click="session.action('accept')"
              >
                <span class="action-icon">✓</span>
                <span>{{ session.actionInFlight.value ? "保存中…" : "采纳" }}</span>
              </button>
            </div>
          </template>

          <!-- ========== done ========== -->
          <div v-else-if="session.phase.value === 'done'" class="state-done">
            <template v-if="focusCharacterName">
              <p class="done-title">这是你心中的【{{ focusCharacterName }}】。</p>
              <p class="done-subtitle">他在《{{ projectName }}》里,和别人心中的不一样。</p>
            </template>
            <template v-else>
              <p class="done-title">这是你心中的《{{ projectName }}》。</p>
              <p class="done-subtitle">它和别人心中的不一样。</p>
            </template>

            <div v-if="session.totalCount.value > 0" class="done-stats">
              采纳 <strong>{{ session.acceptedCount.value }}</strong> ·
              改一改 <strong>{{ session.refinements.value.filter(r => r.status === 'edited').length }}</strong> ·
              不采纳 <strong>{{ session.rejectedCount.value }}</strong>
              <span v-if="session.stats.value" class="cost mono">
                · 消耗 ¥{{ session.stats.value.cost_yuan.toFixed(4) }}
              </span>
            </div>
            <p v-else-if="focusCharacterName" class="done-hint">
              AI 这次对【{{ focusCharacterName }}】没特别建议,这个角色已经够稳了。
            </p>
            <p v-else class="done-hint">AI 这次没看出问题,你的角色已经够好了。</p>

            <button class="primary-btn done-btn" @click="handleFinish">完成</button>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 540px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-8);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}

.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

/* ========== loading ========== */
.state-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-8) 0;
}

.spinner {
  display: flex;
  gap: 6px;
}

.spinner-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: bounce 1.4s ease-in-out infinite both;
}

.spinner-dot:nth-child(1) { animation-delay: -0.32s; }
.spinner-dot:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40%           { transform: scale(1.0); opacity: 1; }
}

.state-title {
  font-size: var(--text-md);
  font-weight: 500;
  color: var(--color-text);
  text-align: center;
}

.state-title--err {
  color: var(--color-danger);
}

.state-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  text-align: center;
  max-width: 360px;
  line-height: var(--line-relaxed);
}

/* ========== error ========== */
.state-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-8) 0;
}

.state-actions {
  display: flex;
  gap: var(--space-3);
}

/* ========== reviewing ========== */
.review-header {
  margin-bottom: var(--space-5);
}

.progress {
  width: 100%;
  height: 4px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--color-accent);
  transition: width var(--duration-base) var(--ease-out);
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: var(--space-2);
}

.progress-count {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.link-btn {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: none;
  border: none;
  padding: 0;
}

.link-btn:hover {
  color: var(--color-text);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.suggestion-card {
  padding: var(--space-5);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-5);
}

.suggestion-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.char-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.kind-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}

.kind-chip--warn {
  color: var(--color-warning);
  background: var(--color-warning-soft);
}

.suggestion-text {
  font-size: var(--text-base);
  line-height: var(--line-relaxed);
  color: var(--color-text);
  margin-bottom: var(--space-3);
  white-space: pre-wrap;
  word-break: break-word;
}

.payload-preview {
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-surface);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.warn-hint {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-warning);
}

/* Sprint 6.A2 M1:evolution_hint 提示样式(紫色,与矛盾警告区分;无左侧色条)*/
.evolution-hint {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
  line-height: 1.6;
}
.evolution-hint strong {
  color: var(--color-accent-text);
}
/* 3 按钮 action-bar 变体:横向 3 等分,按钮稍小适应文案 */
.action-bar--evolution {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-2);
}
.action-bar--evolution .action-btn {
  font-size: var(--text-xs);
  padding: 8px 10px;
}
.action-btn--evolution-add {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  border-color: var(--color-accent);
}
.action-btn--evolution-add:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}

/* ========== inline edit ========== */
.edit-pane {
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.edit-label {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}

.edit-textarea {
  width: 100%;
  padding: var(--space-3);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: var(--line-relaxed);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  outline: none;
  resize: vertical;
  min-height: 80px;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}

.edit-textarea:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* ========== 三按钮 ========== */
.action-bar {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.action-btn {
  flex: 1;
  min-width: 100px;
  padding: var(--space-3);
  font-size: var(--text-sm);
  font-weight: 500;
  border-radius: var(--radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  transition: all var(--duration-fast) var(--ease-out);
  border: 1px solid var(--color-border-strong);
  background: var(--color-surface);
  color: var(--color-text);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-btn--reject:hover:not(:disabled) {
  background: var(--color-surface-hover);
}

.action-btn--edit:hover:not(:disabled) {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}

.action-btn--accept {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}

.action-btn--accept:hover:not(:disabled) {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}

.action-icon {
  font-size: var(--text-lg);
  line-height: 1;
}

/* ========== done ========== */
.state-done {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-6) 0 var(--space-4);
  text-align: center;
}

.done-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  letter-spacing: 0;
}

.done-subtitle {
  font-size: var(--text-base);
  color: var(--color-accent-text);
}

.done-stats {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

.done-stats strong {
  color: var(--color-text);
}

.cost {
  margin-left: var(--space-2);
}

.done-hint {
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

.done-btn {
  margin-top: var(--space-5);
}

/* ========== 通用按钮 ========== */
.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-md);
}

.ghost-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

</style>
