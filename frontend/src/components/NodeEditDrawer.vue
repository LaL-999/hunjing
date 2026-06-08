<script setup lang="ts">
/**
 * NodeEditDrawer — 3D 图谱节点的编辑 / 创建抽屉(Sprint 1.M.1.A + .B + .D + .F)。
 *
 * 双模式(单组件保字段对齐):
 *   mode="edit"   点既有节点 → GET 拉数据 → blur 字段自动 PATCH(像 Notion 那样隐形)
 *                  底部"删除"按钮
 *   mode="create" 右键菜单选「新角色 / 新事件」 → 不调 GET、字段全空 → 用户填好按
 *                  「创建」才 POST;按「取消」/ Esc / 关 → 一概不创建,杜绝"未命名孤儿节点"
 *
 * 字段(对齐 ProjectView 主页卡片表单 + 后端 Create*Request schema):
 *   PERSON:name(必填) / identity / personality / quotes(每行一句) / no_go_list(每行一条)
 *   EVENT :description(必填) / participants(多选 chip)
 *
 * Props:
 *   open                       boolean
 *   nodeId                     string|null   edit 模式必填;create 模式 null
 *   nodeType                   'PERSON'|'EVENT'|'OTHER'
 *   charactersForParticipants  EVENT 多选用的全角色列表(从父传)
 *   mode                       'edit' | 'create'  默认 'edit'
 *   projectId                  string  create 模式必填(POST 端点要)
 *
 * Emits:
 *   close      用户关闭(create 模式取消也走这条;父组件不会创建任何节点)
 *   updated    edit 模式某条字段保存成功 → 父组件 reload graphData
 *   deleted    edit 模式删除成功 → 父组件 reload + 关抽屉
 *   created    create 模式 POST 成功 → 父组件 reload + 关抽屉,payload 带新节点 + 类型
 */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import {
  ApiError,
  type BehaviorBaseline,
  type Character,
  type CreateCharacterRequest,
  type CreateEventRequest,
  type ProjectEvent,
  type UpdateCharacterRequest,
  type UpdateEventRequest,
} from "../api/types";
import BehaviorBaselineEditor from "./BehaviorBaselineEditor.vue";
import SkeletonBlock from "./SkeletonBlock.vue";

type NodeKind = "PERSON" | "EVENT" | "OTHER";
type DrawerMode = "edit" | "create";

const router = useRouter();

interface CharacterRef {
  id: string;
  name: string;
}

const props = withDefaults(
  defineProps<{
    open: boolean;
    nodeId: string | null;
    nodeType: NodeKind;
    charactersForParticipants?: CharacterRef[];
    mode?: DrawerMode;
    projectId?: string;
    /**
     * 1.M.3:近期一条已 done 的推演,且当前 PERSON 节点参与了它。
     * 父组件(ProjectGraphView)在 useSimulation done 后,把 simId + label
     * 透传过来;drawer 在 PERSON edit 模式底部显"✨ 刚出现在《XX》→ 查看"。
     * null/undefined → 不显 chip(默认正常 edit 体验)
     */
    recentSimulationId?: string | null;
    recentSimulationLabel?: string;
  }>(),
  { mode: "edit" },
);

const emit = defineEmits<{
  (e: "close"): void;
  (e: "updated"): void;
  (e: "deleted"): void;
  (e: "created", payload:
    | { type: "PERSON"; node: Character }
    | { type: "EVENT"; node: ProjectEvent }
  ): void;
  /**
   * 1.M.2:edit 模式下用户点「✦ AI 对焦」按钮,父组件用 character_id + name
   * 打开 CharacterFocusModal 并传 focusCharacterId 过去客户端 filter。
   */
  (e: "request-focus", payload: { characterId: string; characterName: string }): void;
}>();

// ============================================================
// 加载 + 本地编辑 state
// ============================================================

const loading = ref(false);
const errorMsg = ref<string | null>(null);
const submitting = ref(false);   // create 模式提交锁

// PERSON 字段
const editName = ref("");
const editIdentity = ref("");
const editPersonality = ref("");
const editQuotesRaw = ref("");
const editNoGoRaw = ref("");
// Sprint 6.A2 FOCUS.6(2026-05-22):3D 图谱 drawer 也可编辑 behavior_baseline 4 维
// 填补"AI 自检 + AI 对焦在用,中/末态 UI 看不见"的割裂
const editBaseline = ref<BehaviorBaseline | null>(null);

// P1.B(2026-05-24):角色生命/物理状态锁定 — 治"已死角色复活"瑕疵
// life_status 影响 hard_constraints 注入,deceased 强制铁律不许出现
type LifeStatus = "alive" | "deceased" | "in_facility" | "absent" | "unknown";
const editLifeStatus = ref<LifeStatus>("alive");
const editStatusNote = ref("");

// EVENT 字段
const editDesc = ref("");
const editParticipants = ref<string[]>([]);

// 原始字段(edit 模式检测无变化跳过 PATCH;create 模式始终 null)
let baselinePerson: Character | null = null;
let baselineEvent: ProjectEvent | null = null;

function parseList(raw: string): string[] {
  return raw
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .slice(0, 20);
}

/** Sprint 6.A2 FOCUS.6:behavior_baseline 4 字段相等判定(同 ProjectView 的 behaviorBaselineEqual)*/
function baselineEqual(
  a: BehaviorBaseline | null,
  b: BehaviorBaseline | null,
): boolean {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  if ((a.speech_register ?? null) !== (b.speech_register ?? null)) return false;
  if ((a.emotional_intensity ?? null) !== (b.emotional_intensity ?? null)) return false;
  if ((a.moral_compass ?? null) !== (b.moral_compass ?? null)) return false;
  if (!listsEqual(
    a.out_of_baseline_examples ?? [],
    b.out_of_baseline_examples ?? [],
  )) return false;
  return true;
}

function listsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function resetFields() {
  editName.value = "";
  editIdentity.value = "";
  editPersonality.value = "";
  editQuotesRaw.value = "";
  editNoGoRaw.value = "";
  editBaseline.value = null;
  editLifeStatus.value = "alive";  // P1.B
  editStatusNote.value = "";
  editDesc.value = "";
  editParticipants.value = [];
  baselinePerson = null;
  baselineEvent = null;
  errorMsg.value = null;
  submitting.value = false;
}

async function loadNode() {
  if (!props.nodeId || props.nodeType === "OTHER") {
    return;
  }
  loading.value = true;
  errorMsg.value = null;
  try {
    if (props.nodeType === "PERSON") {
      const c = await api.get<Character>(`/characters/${props.nodeId}`);
      baselinePerson = c;
      editName.value = c.name;
      editIdentity.value = c.identity;
      editPersonality.value = c.personality;
      editQuotesRaw.value = (c.quotes ?? []).join("\n");
      editNoGoRaw.value = (c.no_go_list ?? []).join("\n");
      editBaseline.value = c.behavior_baseline ?? null;
      // P1.B(2026-05-24):生命/物理状态(老 db 行兜底 alive/'')
      editLifeStatus.value = (c.life_status as LifeStatus) ?? "alive";
      editStatusNote.value = c.status_note ?? "";
    } else if (props.nodeType === "EVENT") {
      const ev = await api.get<ProjectEvent>(`/events/${props.nodeId}`);
      baselineEvent = ev;
      editDesc.value = ev.description;
      editParticipants.value = [...ev.participants];
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.nodeId, props.nodeType, props.mode],
  ([isOpen]) => {
    if (!isOpen) {
      resetFields();
      return;
    }
    if (props.mode === "create") {
      // 创建模式:不 GET,字段全空开局
      resetFields();
      return;
    }
    // edit 模式:正常拉数据
    if (props.nodeId) {
      void loadNode();
    }
  },
  { immediate: true },
);

// ============================================================
// PATCH 持久化(对齐 ProjectView 字段保存逻辑)— 仅 edit 模式
// ============================================================

async function savePersonField() {
  if (props.mode === "create") return;   // create 模式不 blur 自动保存
  if (!baselinePerson || !props.nodeId) return;
  const name = editName.value.trim();
  const identity = editIdentity.value.trim();
  const personality = editPersonality.value.trim();
  const quotes = parseList(editQuotesRaw.value);
  const noGo = parseList(editNoGoRaw.value);

  if (!name) {
    errorMsg.value = "名字不能为空";
    return;
  }

  const baselineChanged = !baselineEqual(
    editBaseline.value,
    baselinePerson.behavior_baseline ?? null,
  );
  // P1.B(2026-05-24):life_status / status_note 变更检测
  const baselineLifeStatus = (baselinePerson.life_status as LifeStatus) ?? "alive";
  const baselineStatusNote = baselinePerson.status_note ?? "";
  const lifeStatusChanged = editLifeStatus.value !== baselineLifeStatus;
  const statusNoteChanged = editStatusNote.value !== baselineStatusNote;

  if (
    name === baselinePerson.name &&
    identity === baselinePerson.identity &&
    personality === baselinePerson.personality &&
    listsEqual(quotes, baselinePerson.quotes ?? []) &&
    listsEqual(noGo, baselinePerson.no_go_list ?? []) &&
    !baselineChanged &&
    !lifeStatusChanged &&
    !statusNoteChanged
  ) {
    return;
  }

  const body: UpdateCharacterRequest = {};
  if (name !== baselinePerson.name) body.name = name;
  if (identity !== baselinePerson.identity) body.identity = identity;
  if (personality !== baselinePerson.personality) body.personality = personality;
  if (!listsEqual(quotes, baselinePerson.quotes ?? [])) body.quotes = quotes;
  if (!listsEqual(noGo, baselinePerson.no_go_list ?? [])) body.no_go_list = noGo;
  if (baselineChanged) body.behavior_baseline = editBaseline.value;
  // P1.B:角色状态独立变更也提交
  if (lifeStatusChanged) body.life_status = editLifeStatus.value;
  if (statusNoteChanged) body.status_note = editStatusNote.value;

  errorMsg.value = null;
  try {
    const updated = await api.patch<Character>(
      `/characters/${props.nodeId}`,
      body,
    );
    baselinePerson = updated;
    editName.value = updated.name;
    editIdentity.value = updated.identity;
    editPersonality.value = updated.personality;
    editQuotesRaw.value = (updated.quotes ?? []).join("\n");
    editNoGoRaw.value = (updated.no_go_list ?? []).join("\n");
    editBaseline.value = updated.behavior_baseline ?? null;
    // P1.B:同步刷新本地状态值(后端返回的标准化值)
    editLifeStatus.value = (updated.life_status as LifeStatus) ?? "alive";
    editStatusNote.value = updated.status_note ?? "";
    emit("updated");
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "保存失败";
  }
}

async function saveEventField() {
  if (props.mode === "create") return;
  if (!baselineEvent || !props.nodeId) return;
  const desc = editDesc.value.trim();
  if (!desc) {
    errorMsg.value = "事件描述不能为空";
    return;
  }
  const newParts = [...editParticipants.value].sort();
  const oldParts = [...baselineEvent.participants].sort();
  if (
    desc === baselineEvent.description &&
    JSON.stringify(newParts) === JSON.stringify(oldParts)
  ) {
    return;
  }
  const body: UpdateEventRequest = {};
  if (desc !== baselineEvent.description) body.description = desc;
  if (JSON.stringify(newParts) !== JSON.stringify(oldParts)) {
    body.participants = [...editParticipants.value];
  }

  errorMsg.value = null;
  try {
    const updated = await api.patch<ProjectEvent>(
      `/events/${props.nodeId}`, body,
    );
    baselineEvent = updated;
    editDesc.value = updated.description;
    editParticipants.value = [...updated.participants];
    emit("updated");
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "保存失败";
  }
}

function toggleParticipant(charId: string) {
  const i = editParticipants.value.indexOf(charId);
  if (i >= 0) editParticipants.value.splice(i, 1);
  else editParticipants.value.push(charId);
  // create 模式只本地切;edit 模式调 save
  if (props.mode === "edit") void saveEventField();
}

// ============================================================
// 创建提交(create 模式专用)— POST 完整字段
// ============================================================

async function submitCreatePerson() {
  if (props.mode !== "create" || submitting.value) return;
  if (!props.projectId) {
    errorMsg.value = "缺少 projectId,无法创建";
    return;
  }
  const name = editName.value.trim();
  if (!name) {
    errorMsg.value = "名字不能为空";
    return;
  }
  errorMsg.value = null;
  submitting.value = true;
  try {
    const body: CreateCharacterRequest = {
      name,
      identity: editIdentity.value.trim(),
      personality: editPersonality.value.trim(),
      quotes: parseList(editQuotesRaw.value),
      no_go_list: parseList(editNoGoRaw.value),
      // FOCUS.6(2026-05-22):create 模式也带 baseline(用户创建角色时若已经设了 4 维)
      behavior_baseline: editBaseline.value,
      // P1.B(2026-05-24):创建时也带生命/物理状态
      life_status: editLifeStatus.value,
      status_note: editStatusNote.value,
    };
    const created = await api.post<Character>(
      `/projects/${props.projectId}/characters`,
      body,
    );
    emit("created", { type: "PERSON", node: created });
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "创建失败";
  } finally {
    submitting.value = false;
  }
}

async function submitCreateEvent() {
  if (props.mode !== "create" || submitting.value) return;
  if (!props.projectId) {
    errorMsg.value = "缺少 projectId,无法创建";
    return;
  }
  const desc = editDesc.value.trim();
  if (!desc) {
    errorMsg.value = "事件描述不能为空";
    return;
  }
  errorMsg.value = null;
  submitting.value = true;
  try {
    const body: CreateEventRequest = {
      description: desc,
      participants: [...editParticipants.value],
    };
    const created = await api.post<ProjectEvent>(
      `/projects/${props.projectId}/events`,
      body,
    );
    emit("created", { type: "EVENT", node: created });
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "创建失败";
  } finally {
    submitting.value = false;
  }
}

function cancelCreate() {
  if (submitting.value) return;
  emit("close");   // 父组件听 close;create 模式 close = 不创建任何节点
}

// ============================================================
// 删除(仅 edit 模式)
// ============================================================

const deleting = ref(false);
async function handleDelete() {
  if (props.mode !== "edit" || !props.nodeId) return;
  // 2026-06-02 hotfix:重入保护,防快速双击触发两次 confirmDialog
  if (deleting.value) return;
  const isPersonDel = props.nodeType === "PERSON";
  const isEventDel = props.nodeType === "EVENT";
  if (!isPersonDel && !isEventDel) return;
  const name = isPersonDel
    ? baselinePerson?.name
    : baselineEvent?.description?.slice(0, 16);
  const tip = isPersonDel
    ? "关联的关系会一并清除"
    : "事件将从图谱移除";
  const ok = await confirmDialog({
    title: `删除「${name ?? "此节点"}」?`,
    message: tip,
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;

  deleting.value = true;
  try {
    if (isPersonDel) {
      await api.delete(`/characters/${props.nodeId}`);
    } else {
      await api.delete(`/events/${props.nodeId}`);
    }
    emit("deleted");
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "删除失败";
  } finally {
    deleting.value = false;
  }
}

// ============================================================
// 键盘 / 关闭策略
// ============================================================

function handleKey(e: KeyboardEvent) {
  if (e.key === "Enter") {
    if ((e.target as HTMLElement).tagName === "TEXTAREA") return;
    e.preventDefault();
    if (props.mode === "create") {
      // create 模式 Enter = 提交
      if (props.nodeType === "PERSON") void submitCreatePerson();
      else if (props.nodeType === "EVENT") void submitCreateEvent();
    } else {
      // edit 模式 Enter = blur 触发自动保存
      (e.target as HTMLElement).blur();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

// 一些 textarea 自动撑高的指令
const vAutogrow = {
  mounted(el: HTMLTextAreaElement) {
    const grow = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    requestAnimationFrame(grow);
    el.addEventListener("input", grow);
  },
  updated(el: HTMLTextAreaElement) {
    requestAnimationFrame(() => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    });
  },
};

// ============================================================
// 派生
// ============================================================

const isPerson = computed(() => props.nodeType === "PERSON");
const isEvent = computed(() => props.nodeType === "EVENT");
const isCreate = computed(() => props.mode === "create");

const personalityCount = computed(() => editPersonality.value.length);

// Sprint 6.A1(2026-05-18):AI 一键补全 agent 档案
const enriching = ref(false);
const lastEnrichInfo = ref<string | null>(null);

async function handleEnrichAgentProfile() {
  if (enriching.value || !props.nodeId) return;
  enriching.value = true;
  lastEnrichInfo.value = null;
  try {
    type EnrichResp = {
      character: Character;
      report: {
        updated_fields: string[];
        skipped_fields: string[];
        context_samples: number;
        error: string | null;
      };
    };
    const resp = await api.post<EnrichResp>(
      `/characters/${props.nodeId}/enrich_agent_profile`,
    );
    // 把补全后的字段回填到 v-model(reload from server)
    editIdentity.value = resp.character.identity;
    editPersonality.value = resp.character.personality;
    editQuotesRaw.value = (resp.character.quotes ?? []).join("\n");
    editNoGoRaw.value = (resp.character.no_go_list ?? []).join("\n");
    editBaseline.value = resp.character.behavior_baseline ?? null;
    const updated = resp.report.updated_fields;
    if (updated.length > 0) {
      lastEnrichInfo.value = `✓ AI 补全了:${updated.join(" / ")}(参考 ${resp.report.context_samples} 段原文)`;
      toast.success(`agent 档案补全完成 (${updated.length} 字段)`);
    } else {
      lastEnrichInfo.value = "所有字段都已填,无需补全";
      toast.info("4 字段都已填好");
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : "补全失败";
    lastEnrichInfo.value = `✗ ${msg}`;
    toast.error(msg);
  } finally {
    enriching.value = false;
  }
}

const headerTitle = computed(() => {
  if (isCreate.value) {
    return isPerson.value ? "新角色" : isEvent.value ? "新事件" : "新节点";
  }
  return isPerson.value ? "角色" : isEvent.value ? "事件" : "节点";
});

const personSubmitDisabled = computed(
  () => submitting.value || editName.value.trim().length === 0,
);
const eventSubmitDisabled = computed(
  () => submitting.value || editDesc.value.trim().length === 0,
);

// 1.M.3:recent simulation chip 点击 → 跳详情页
function gotoRecentSimulation() {
  if (!props.recentSimulationId) return;
  router.push(`/simulations/${props.recentSimulationId}`);
}
</script>

<template>
  <Teleport to="body">
    <transition name="drawer-slide">
      <!-- 2026-05 P1:v-if → v-show — 首次 open 后组件 mount 一直保留,
           后续切节点只换 props.nodeId 不重建 → 第二次以后打开 ~30ms 而非 ~360ms。
           transition 配合 v-show 走 display: none 切换,enter/leave 动画照旧。 -->
      <aside
        v-show="open"
        class="drawer"
        role="dialog"
        aria-label="节点编辑"
        @keydown="handleKey"
      >
        <header class="drawer-header">
          <span class="node-type-chip" :class="`chip-${nodeType.toLowerCase()}`">
            {{ headerTitle }}
          </span>
          <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">×</button>
        </header>

        <div v-if="loading" class="loading-skeleton" aria-busy="true" aria-live="polite">
          <SkeletonBlock height="14px" width="40%" />
          <SkeletonBlock height="38px" />
          <SkeletonBlock height="14px" width="50%" />
          <SkeletonBlock height="38px" />
          <SkeletonBlock height="14px" width="35%" />
          <SkeletonBlock height="72px" />
        </div>

        <div v-else-if="errorMsg" class="error-banner">
          {{ errorMsg }}
        </div>

        <!-- PERSON 完整 inline 编辑表单(对齐 ProjectView 行编辑) -->
        <div v-if="isPerson && !loading" class="form">
          <label class="field">
            <span class="field-label">名字</span>
            <input
              v-model="editName"
              type="text"
              maxlength="20"
              class="row-input"
              autofocus
              :placeholder="isCreate ? '如:林晚' : ''"
              @blur="savePersonField"
            />
          </label>

          <!-- Sprint 6.A1(2026-05-18):4 字段语义重定位为"agent 档案" — 续写时这个角色
               作为独立 agent 扮演时会用到。原作角色由 AI 抽完图谱后自动补全;手建角色可
               手动填或点底部"AI 补全档案"按钮触发 LLM 补全空字段(已填的不覆盖)。-->
          <div class="agent-profile-banner" v-if="!isCreate">
            <span class="banner-icon" aria-hidden="true">🎭</span>
            <span class="banner-text">
              下方 4 字段是 <strong>agent 档案</strong> — 续写时角色作为独立 agent 扮演时使用。
            </span>
          </div>

          <label class="field">
            <span class="field-label">身份(我是谁)</span>
            <input
              v-model="editIdentity"
              type="text"
              maxlength="200"
              class="row-input"
              placeholder="如:贾府二老爷,贾母长子,管家事"
              @blur="savePersonField"
            />
          </label>

          <label class="field">
            <span class="field-label">
              性格(我会怎么做选择)
              <span class="field-hint">{{ personalityCount }} / 500</span>
            </span>
            <textarea
              v-autogrow
              v-model="editPersonality"
              class="row-input row-textarea"
              maxlength="500"
              rows="2"
              placeholder="如:在乎家族体面,遇事先想 reputation 再想个人;不爱正面冲突"
              @blur="savePersonField"
            ></textarea>
          </label>

          <label class="field">
            <span class="field-label">
              台词风格(我说话什么语气)
              <span class="field-hint">每行一句模仿原作语气示例,最多 20</span>
            </span>
            <textarea
              v-autogrow
              v-model="editQuotesRaw"
              class="row-input row-textarea"
              rows="3"
              placeholder="例:我们这老婆子,什么没经过的&#10;你这孩子,叫人看着就心疼"
              @blur="savePersonField"
            ></textarea>
          </label>

          <label class="field">
            <span class="field-label">
              禁忌(我一般不会做的事)
              <span class="field-hint">每行一条,最多 20 — "一般"留弹性,极端剧情可破例</span>
            </span>
            <textarea
              v-autogrow
              v-model="editNoGoRaw"
              class="row-input row-textarea"
              rows="2"
              placeholder="例:不主动谈金钱&#10;不公开偏袒某个孙辈伤害另一方颜面"
              @blur="savePersonField"
            ></textarea>
          </label>

          <!--
            Sprint 6.A2 FOCUS.6(2026-05-22):3D 图谱 drawer 也可编辑 behavior_baseline 4 维。
            填补"AI 自检 + AI 对焦在用,3D drawer 看不见"的割裂。
            edit 模式 + create 模式都支持(create 模式 baselinePerson 为 null,@save 走 createCharacter 分支)。
          -->
          <BehaviorBaselineEditor
            :baseline="editBaseline"
            @update:baseline="editBaseline = $event"
            @save="savePersonField"
          />

          <!-- P1.B(2026-05-24):角色生命/物理状态锁定 — 治"已死角色复活"瑕疵
               例:挪威森林续作里直子(已自杀)和绿子在阁楼同框 → 违反原作时间线
               设为 deceased 后,任何场景都不许出现该角色 -->
          <div class="life-status-block">
            <label class="field">
              <span class="field-label">
                生命 / 物理状态
                <span class="field-hint">续写时硬约束 — 死者不复活、异地者不同框</span>
              </span>
              <select
                v-model="editLifeStatus"
                class="row-input"
                @change="savePersonField"
              >
                <option value="alive">在世 — 自由出场(默认)</option>
                <option value="deceased">已死亡 — 任何场景不许出现</option>
                <option value="in_facility">在特定地点 — 异地隔离(疗养院/监狱/出国)</option>
                <option value="absent">暂时离开 — 类似异地</option>
                <option value="unknown">未知 — 不约束</option>
              </select>
            </label>
            <label v-if="editLifeStatus !== 'alive' && editLifeStatus !== 'unknown'" class="field">
              <span class="field-label">
                状态备注
                <span class="field-hint">例:"在阿美寮疗养院" / "1969年自杀" / "已被流放德国"</span>
              </span>
              <input
                v-model="editStatusNote"
                type="text"
                maxlength="200"
                class="row-input"
                placeholder="补充状态描述,给 LLM 看的人物状态注释"
                @blur="savePersonField"
              />
            </label>
          </div>

          <!-- Sprint 6.A1:AI 一键补全 agent 档案(已填字段不覆盖)-->
          <div v-if="!isCreate" class="enrich-row">
            <button
              type="button"
              class="ghost-btn enrich-btn"
              :disabled="enriching"
              :title="
                'LLM 根据原作上下文补全 4 字段中空着的部分(已填内容不动)。' +
                '初始态没有原作时也可用 — LLM 凭名字推断通用档案。'
              "
              @click="handleEnrichAgentProfile"
            >
              <span v-if="enriching">补全中…</span>
              <span v-else>✦ AI 补全 agent 档案</span>
            </button>
            <span v-if="lastEnrichInfo" class="enrich-info">
              {{ lastEnrichInfo }}
            </span>
          </div>

          <!-- create 模式:显式按钮;edit 模式:hint + 删除 -->
          <template v-if="isCreate">
            <p class="hint">填好后点「创建」 · Esc 取消(不会创建任何节点)</p>
            <div class="action-row">
              <button
                type="button"
                class="ghost-btn"
                :disabled="submitting"
                @click="cancelCreate"
              >取消</button>
              <button
                type="button"
                class="primary-btn"
                :disabled="personSubmitDisabled"
                @click="submitCreatePerson"
              >{{ submitting ? "创建中…" : "创建" }}</button>
            </div>
          </template>
          <template v-else>
            <p class="hint">点击其它字段或抽屉外自动保存 · Esc 关闭</p>

            <!-- 1.M.3:刚演过的"光迹" chip — 点跳详情;只有 recentSimulationId 非空才显 -->
            <button
              v-if="recentSimulationId"
              type="button"
              class="recent-sim-chip"
              @click="gotoRecentSimulation"
            >
              <span class="chip-spark">✨</span>
              刚出现在《{{ recentSimulationLabel || "推演" }}》→ 查看
            </button>

            <!-- 1.M.2:AI 对焦上下文按钮 — 仅 PERSON edit 模式且角色已加载完毕。
                 baselinePerson 是 let 不响应式,不能模板里 v-if;改用 props.nodeId
                 + editName(已 loadNode 填充)走响应式路径。 -->
            <button
              v-if="nodeId"
              type="button"
              class="focus-btn"
              :disabled="deleting"
              @click="emit('request-focus', { characterId: nodeId, characterName: editName })"
            >
              <span class="focus-icon">✦</span>
              AI 对焦【{{ editName }}】
            </button>

            <button
              type="button"
              class="del-btn"
              :disabled="deleting"
              @click="handleDelete"
            >
              {{ deleting ? "删除中…" : "删除该角色" }}
            </button>
          </template>
        </div>

        <!-- EVENT 完整 inline 编辑(Sprint 1.M.1.D)
             2026-05-12 修复:`v-if` → `v-else-if`,接上面 PERSON 表单 的 v-if 链,
             否则 isPerson=true 时这里 v-if 为 false,fallback "不支持编辑" 误显 -->
        <div v-else-if="isEvent && !loading" class="form">
          <label class="field">
            <span class="field-label">事件描述</span>
            <input
              v-model="editDesc"
              type="text"
              maxlength="300"
              class="row-input"
              placeholder="如:风雨夜逢于客栈"
              autofocus
              @blur="saveEventField"
              @keydown.enter.prevent="(e) => (e.target as HTMLElement).blur()"
              @keydown.esc.prevent="emit('close')"
            />
          </label>

          <div class="field">
            <span class="field-label">
              参与角色
              <span class="field-hint">点击切换</span>
            </span>
            <div
              v-if="charactersForParticipants && charactersForParticipants.length"
              class="participants"
            >
              <button
                type="button"
                v-for="c in charactersForParticipants"
                :key="c.id"
                class="participant-chip"
                :class="{ 'is-active': editParticipants.includes(c.id) }"
                @click="toggleParticipant(c.id)"
              >{{ c.name }}</button>
            </div>
            <p v-else class="field-hint">项目还没有角色</p>
          </div>

          <template v-if="isCreate">
            <p class="hint">填好后点「创建」 · Esc 取消(不会创建任何节点)</p>
            <div class="action-row">
              <button
                type="button"
                class="ghost-btn"
                :disabled="submitting"
                @click="cancelCreate"
              >取消</button>
              <button
                type="button"
                class="primary-btn"
                :disabled="eventSubmitDisabled"
                @click="submitCreateEvent"
              >{{ submitting ? "创建中…" : "创建" }}</button>
            </div>
          </template>
          <template v-else>
            <p class="hint">点击其它字段或抽屉外自动保存 · Esc 关闭</p>
            <button
              type="button"
              class="del-btn"
              :disabled="deleting"
              @click="handleDelete"
            >{{ deleting ? "删除中…" : "删除该事件" }}</button>
          </template>
        </div>

        <!-- OTHER 节点(理论上不应发生)-->
        <div v-else-if="!loading" class="placeholder">
          这个节点类型暂不支持图谱内编辑。
        </div>
      </aside>
    </transition>
  </Teleport>
</template>

<style scoped>
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 380px;
  max-width: 100vw;
  z-index: var(--z-modal);
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: -4px 0 12px rgba(0, 0, 0, 0.05);
  padding: var(--space-5) var(--space-5) var(--space-6);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.node-type-chip {
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.chip-person {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}
.chip-event {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}

.close-btn {
  width: 28px;
  height: 28px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.loading-state,
.placeholder {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}

/* Sprint 6.A2 polish(2026-05-22):loading 改 SkeletonBlock 占位
 * 对齐 form 字段结构 — label(14px) + input(38px) × 2 + textarea(72px),
 * 比"加载中…" 纯文字更直观,降低用户"卡住了"的焦虑 */
.loading-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4) 0;
}

.error-banner {
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.field-hint {
  color: var(--color-text-subtle);
  font-weight: 400;
}

.row-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
  font-family: inherit;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.row-input:focus {
  border-color: var(--color-accent);
  background: var(--color-surface);
}

.row-textarea {
  resize: none;
  overflow: hidden;
  line-height: 1.5;
  min-height: 32px;
}

.hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
}

/* Sprint 6.A1(2026-05-18):agent 档案 banner + 补全按钮区 */
.agent-profile-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
}
.banner-icon {
  font-size: var(--text-md);
  flex-shrink: 0;
}
.banner-text {
  flex: 1;
  line-height: 1.5;
}

.enrich-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  flex-wrap: wrap;
}
.enrich-btn {
  flex-shrink: 0;
}
.enrich-info {
  flex: 1;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
  min-width: 0;
}

.participants {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.participant-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  transition: all var(--duration-fast) var(--ease-out);
}
.participant-chip:hover {
  border-color: var(--color-accent-border);
}
.participant-chip.is-active {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

/* 1.M.3:recent simulation chip — 比 focus-btn 更轻量(底色 bg-subtle),触感不抢戏 */
.recent-sim-chip {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  transition: all var(--duration-fast) var(--ease-out);
  text-align: left;
}
.recent-sim-chip:hover {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
  border-style: solid;
}
.chip-spark {
  font-size: var(--text-sm);
}

/* 1.M.2:AI 对焦按钮 — 用 accent 色调与 hint 区分 */
.focus-btn {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  transition: all var(--duration-fast) var(--ease-out);
}
.focus-btn:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.focus-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.focus-icon {
  font-size: var(--text-md);
  line-height: 1;
}

.del-btn {
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: transparent;
  border: 1px solid var(--color-danger-soft);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.del-btn:hover:not(:disabled) {
  background: var(--color-danger-soft);
}
.del-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* create 模式底部按钮行 */
.action-row {
  margin-top: var(--space-3);
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.ghost-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.ghost-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
  opacity: 0.6;
}

</style>
