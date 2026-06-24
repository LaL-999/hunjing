/**
 * useSimulation — 续写引擎前端状态机 + SSE 实时事件流(Sprint 1.L)。
 *
 * 接 Sprint 1.G simulation_service + 1.L SSE 后端。
 *
 * 状态(UI 维度,聚合后端 SimulationState):
 *   idle         初始,modal 未打开
 *   configuring  填配置表单
 *   creating     POST 中(<1s)
 *   running      sim 已创建,SSE 实时接事件
 *   done         state=done,narrative 已可读
 *   failed       state=failed,error_message 已落库
 *   error        本地错误(POST 失败、网络等),与 failed 区分
 *
 * 数据获取策略:
 *   1. POST /simulations 或 subscribe(id) 拿到 sim_id
 *   2. GET /simulations/{id} 一次拉完整详情(含 timeline / characters_snapshot)
 *   3. 非终态时,POST /auth/sse_token 拿短期 token,new EventSource 接收实时事件
 *   4. SSE 异常 → fallback 2s 轮询 GET 详情(降级保活)
 *   5. 终态(done / error / cancelled)→ 关 SSE,如果是 done 再补拉一次完整详情
 *      (因为 done event 字段有限,timeline 等仍需 GET)
 *
 * 事件流(events):最近 50 条原始 event,UI 用来呈现 PowerShell 风格滚动日志。
 * 配额错误 (QUOTA_EXCEEDED) 单独暴露 errorDetail,父组件 catch 后弹 UpgradeModal。
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CreateSimulationRequest,
  type SimulationCreatedResponse,
  type SimulationFull,
  type SimulationState,
} from "../api/types";

const FALLBACK_POLL_INTERVAL_MS = 2000;
const MAX_EVENTS_KEPT = 50;

export type SimulationPhase =
  | "idle"
  | "configuring"
  | "creating"
  | "running"
  | "done"
  | "failed"
  | "error";

/** SSE 推送的单条事件(对齐 backend simulation_service._emit_event +
 *  agent_evolution_engine._emit)
 *
 *  quick 模式事件:snapshot / state_change / round_start / director_* /
 *  agent_* / round_done / composing_* / done / error
 *
 *  evolution 模式事件(Sprint 6.A2 M3.B):evolution_start /
 *  evolution_scene_start / evolution_scene_picked / evolution_scene_skipped /
 *  evolution_agents_summoned / evolution_rag_retrieved /
 *  evolution_agent_start / evolution_agent_done / evolution_scene_done /
 *  evolution_target_reached / done / error
 */
export interface SimulationEvent {
  kind: string;
  round?: number;
  rounds_planned?: number;
  speaker?: string;
  duration_ms?: number;
  tokens_input?: number;
  tokens_output?: number;
  cost_yuan?: number;
  state?: string;
  message?: string;
  narrative?: string;
  narrative_chars?: number;
  dialogue_preview?: string;
  location?: string;
  time_advance?: string;
  speaking_agents?: string[];
  /** snapshot 独有 */
  id?: string;
  current_round?: number;
  error_message?: string | null;
  /** narrator 合稿多候选 — 0-based 当前候选 index */
  sample_index?: number;
  // ============================================================
  // Sprint 6.A2 M3.B 灵魂续写 evolution_* 字段
  // ============================================================
  /** 主入口:evolution_start / evolution_target_reached / done 携带 */
  target_chars?: number;
  /** evolution_start:本次最多跑几幕(动态停止上限) */
  max_scenes?: number;
  /** evolution_start:'quick' | 'evolution' */
  mode?: string;
  /** evolution_scene_*:幕序号 0-based */
  scene_index?: number;
  /** evolution_scene_start:总规划幕数(=rounds_planned) */
  total_planned?: number;
  /** evolution_scene_picked:场景名称 / 来源 / 时间锚 */
  scene_name?: string;
  scene_source?: string;
  time_anchor?: string;
  reasoning?: string;
  /** evolution_scene_skipped:跳过原因 */
  reason?: string;
  /** evolution_agents_summoned:本幕在场角色 */
  agents?: Array<{ id: string; name: string; is_protagonist: boolean }>;
  /** evolution_rag_retrieved:召回的原著片段 */
  chunks?: Array<{ score: number; preview: string }>;
  /** evolution_agent_*:对话轮次 0-based(每幕内) */
  round_index?: number;
  /** evolution_agent_start / done:speaker_id 用于私有记忆链路 */
  speaker_id?: string;
  /** evolution_scene_done:本幕合稿的开头预览 */
  segment_preview?: string;
  /** evolution_target_reached:已完成多少幕 */
  scenes_done?: number;
  /** done(evolution):总幕数 */
  scenes_count?: number;
  /** 客户端补:接收时间戳(UI 排序展示用) */
  _ts?: number;
}

/** 事件流 UI 渲染的中间格式 — Dock / Detail 共用 */
export interface FormattedEvent {
  /** Vue v-for 唯一 key */
  key: string;
  /** 缩进级别 0/1 — director 是 0,agent 是 1 */
  indent: number;
  /** 行首图标(✦ ▸ ✓ ✗ · —) */
  icon: string;
  /** 主文案 */
  main: string;
  /** 副文案(灰,可空) */
  sub: string;
  /** 状态色 */
  tone: "normal" | "success" | "error";
}

function _fmtMs(ms?: number): string {
  if (ms === undefined) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
function _fmtTokens(input?: number, output?: number): string {
  if (input === undefined || output === undefined) return "";
  return `${input + output} tk`;
}

/** 把 SimulationEvent 翻译成 UI 行(null 表示该事件不需要渲染) */
export function formatSimulationEvent(
  ev: SimulationEvent, idx: number,
): FormattedEvent | null {
  const key = `${ev._ts}-${idx}`;
  switch (ev.kind) {
    case "snapshot":
    case "state_change":
      return null;
    case "round_start":
      return {
        key, indent: 0, icon: "▸",
        main: `第 ${ev.round} / ${ev.rounds_planned} 轮`,
        sub: "", tone: "normal",
      };
    case "director_start":
      return {
        key, indent: 0, icon: "✦",
        main: "Director 编排中…", sub: "", tone: "normal",
      };
    case "director_done":
      return {
        key, indent: 0, icon: "✓",
        main: "Director 完成",
        sub: [
          _fmtMs(ev.duration_ms),
          _fmtTokens(ev.tokens_input, ev.tokens_output),
          ev.location,
          ev.speaking_agents?.length
            ? `发声:${ev.speaking_agents.join(" / ")}` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "agent_start":
      return {
        key, indent: 1, icon: "·",
        main: `${ev.speaker} 思考中…`, sub: "", tone: "normal",
      };
    case "agent_done":
      return {
        key, indent: 1, icon: "✓",
        main: `${ev.speaker}`,
        sub: [
          _fmtMs(ev.duration_ms),
          _fmtTokens(ev.tokens_input, ev.tokens_output),
          ev.dialogue_preview ? `"${ev.dialogue_preview}…"` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "round_done":
      return {
        key, indent: 0, icon: "—",
        main: `第 ${ev.round} 轮 完成`,
        sub: ev.cost_yuan !== undefined
          ? `累计 ¥${ev.cost_yuan.toFixed(4)}` : "",
        tone: "normal",
      };
    case "composing_start":
      return {
        key, indent: 0, icon: "✦",
        main: "Composer 编织最终叙事…", sub: "", tone: "normal",
      };
    case "composing_done":
      return {
        key, indent: 0, icon: "✓",
        main: "Composer 完成",
        sub: [
          _fmtMs(ev.duration_ms),
          _fmtTokens(ev.tokens_input, ev.tokens_output),
          ev.narrative_chars ? `${ev.narrative_chars} 字` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "done":
      // mode-aware 措辞由上层 stageLabel/dock 标题承担,此处事件流用中性
      return {
        key, indent: 0, icon: "✓", main: "已完成",
        sub: [
          ev.scenes_count !== undefined ? `${ev.scenes_count} 幕` : "",
          ev.narrative_chars ? `${ev.narrative_chars} 字` : "",
          ev.cost_yuan !== undefined ? `总成本 ¥${ev.cost_yuan.toFixed(4)}` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "error":
      return {
        key, indent: 0, icon: "✗",
        main: "已失败", sub: ev.message ?? "", tone: "error",
      };
    // ============================================================
    // Sprint 6.A2 M3.B 灵魂续写 evolution_* 事件
    // ============================================================
    case "evolution_start":
      return {
        key, indent: 0, icon: "🎭",
        main: `灵魂续写启动 · 最多 ${ev.max_scenes ?? "?"} 幕`,
        sub: ev.target_chars ? `目标 ${ev.target_chars} 字` : "",
        tone: "normal",
      };
    case "evolution_scene_start":
      return {
        key, indent: 0, icon: "▸",
        main: `第 ${(ev.scene_index ?? 0) + 1} 幕`,
        sub: ev.total_planned ? `/ 上限 ${ev.total_planned}` : "",
        tone: "normal",
      };
    case "evolution_scene_picked": {
      // 场景来源标签:原著场景(pick) / 新创场景(llm_created) / 兜底原值
      let srcLabel = ev.scene_source ?? "";
      if (ev.scene_source === "project_scenes_pick") {
        srcLabel = "原著场景";
      } else if (ev.scene_source === "llm_created") {
        srcLabel = "新创场景";
      }
      const timeSuffix = ev.time_anchor ? ` · ${ev.time_anchor}` : "";
      return {
        key, indent: 0, icon: "📍",
        main: `场景:${ev.scene_name ?? "?"}${timeSuffix}`,
        sub: [srcLabel, ev.reasoning].filter(Boolean).join(" · "),
        tone: "normal",
      };
    }
    case "evolution_scene_skipped":
      return {
        key, indent: 0, icon: "⊘",
        main: `跳过第 ${(ev.scene_index ?? 0) + 1} 幕`,
        sub: ev.reason === "no_agents_available" ? "无可用角色" : (ev.reason ?? ""),
        tone: "normal",
      };
    case "evolution_agents_summoned": {
      const names = (ev.agents ?? []).map((a) => a.name).join(" / ");
      return {
        key, indent: 0, icon: "·",
        main: `在场:${names || "(空)"}`,
        sub: "", tone: "normal",
      };
    }
    case "evolution_rag_retrieved":
      // Sprint 6.A2 polish(2026-05-22):用户反馈"召回 N 段原著" 是技术内部细节无价值,
      // 不在事件流显示;RAG 召回是 agent 推理的子步骤,用户只关心 agent 输出
      return null;
    case "evolution_agent_start":
      return {
        key, indent: 1, icon: "·",
        main: `${ev.speaker} 思考中…`,
        sub: ev.round_index !== undefined
          ? `第 ${ev.round_index + 1} 轮对话` : "",
        tone: "normal",
      };
    case "evolution_agent_done":
      return {
        key, indent: 1, icon: "✓",
        main: `${ev.speaker}`,
        sub: [
          _fmtMs(ev.duration_ms),
          _fmtTokens(ev.tokens_input, ev.tokens_output),
          ev.dialogue_preview ? `"${ev.dialogue_preview}…"` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "evolution_scene_done":
      return {
        key, indent: 0, icon: "—",
        main: `第 ${(ev.scene_index ?? 0) + 1} 幕 合稿完成`,
        sub: [
          ev.segment_preview ? `"${ev.segment_preview}…"` : "",
          ev.cost_yuan !== undefined ? `累计 ¥${ev.cost_yuan.toFixed(4)}` : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    case "evolution_target_reached":
      return {
        key, indent: 0, icon: "🎯",
        main: "已达目标字数,提前收口",
        sub: [
          ev.scenes_done !== undefined ? `共 ${ev.scenes_done} 幕` : "",
          ev.narrative_chars && ev.target_chars
            ? `${ev.narrative_chars} / ${ev.target_chars} 字`
            : "",
        ].filter(Boolean).join(" · "),
        tone: "success",
      };
    default:
      return null;
  }
}

export function useSimulation(getProjectId: () => string) {
  const phase = ref<SimulationPhase>("idle");
  const simulation = ref<SimulationFull | null>(null);
  const errorMessage = ref<string | null>(null);
  const errorCode = ref<string | null>(null);
  /** 配额超限时携带 ApiError.detail,父组件传给 UpgradeModal */
  const errorDetail = ref<unknown>(null);
  /** SSE 实时事件流(最近 N 条,UI 展示用) */
  const events = ref<SimulationEvent[]>([]);

  let eventSource: EventSource | null = null;
  let fallbackTimer: ReturnType<typeof setInterval> | null = null;

  // ============================================================
  // 派生
  // ============================================================

  // M6-fix5(2026-05-20)— 幕内 sub-progress 估算:
  // evolution 每幕约 ~19 次 LLM 调用(9 agent + 3 narrator + 3 checker + 4 extractor)
  // 通过 SSE 事件计数估算当前幕内的 0-1 进度,加权 1/rounds_planned 增加到主进度
  const SUB_STAGE_EVENTS_PER_SCENE = 19;

  /** 计算本幕开始以来收到了多少"幕内推进事件"(不含 state_change 等)。*/
  function _sceneInternalEventCount(): number {
    const sim = simulation.value;
    if (!sim) return 0;
    const currentSceneIdx = sim.current_round; // 0-based 已完成幕数 = 当前幕 idx
    let count = 0;
    // 从尾向头扫,遇到 scene_start 或 done 就停;统计本幕内的事件
    for (let i = events.value.length - 1; i >= 0; i--) {
      const ev = events.value[i];
      if (
        ev.kind === "evolution_scene_start"
        && ev.scene_index === currentSceneIdx
      ) {
        break;
      }
      if (ev.kind === "evolution_scene_done") {
        break; // 上一幕的事件,不算
      }
      // 计入幕内推进事件(过滤心跳 / snapshot / state_change)
      if (
        ev.kind && [
          "evolution_agent_start", "evolution_agent_done",
          "evolution_scene_picked", "evolution_agents_summoned",
          "evolution_rag_retrieved", "evolution_narrator_sample",
          "evolution_narrator_selected", "evolution_narrator_retried",
          "evolution_consistency_checked", "evolution_world_facts_extracted",
          "evolution_plot_threads_tracked", "evolution_entities_registered",
          "evolution_actions_extracted", "evolution_emotional_states_tracked",
        ].includes(ev.kind)
      ) {
        count += 1;
      }
    }
    return count;
  }

  const progress = computed(() => {
    const sim = simulation.value;
    if (!sim) return 0;
    if (sim.state === "done") return 1;
    if (sim.state === "failed" || sim.state === "cancelled") return 0;
    if (sim.rounds_planned === 0) return 0;
    if (sim.state === "composing") return 0.95;

    const sceneBaseProgress = sim.current_round / sim.rounds_planned;

    // M6-fix5:加入幕内 sub-progress(让用户在长幕等待时看到进度持续推进)
    if (sim.mode === "evolution") {
      const internalCount = _sceneInternalEventCount();
      const subProgress = Math.min(1, internalCount / SUB_STAGE_EVENTS_PER_SCENE);
      const sceneWeight = 1 / sim.rounds_planned;
      return Math.min(0.95, sceneBaseProgress + subProgress * sceneWeight);
    }

    return Math.min(0.95, sceneBaseProgress);
  });

  // M6-fix5:已运行时长(秒)— 让用户对长篇有时间感
  const _nowTick = ref(Date.now());
  let _nowTimer: ReturnType<typeof setInterval> | null = null;
  function _startTimer() {
    if (_nowTimer !== null) return;
    _nowTimer = setInterval(() => {
      _nowTick.value = Date.now();
    }, 1000);
  }
  function _stopTimer() {
    if (_nowTimer !== null) {
      clearInterval(_nowTimer);
      _nowTimer = null;
    }
  }

  // Bug 1 治本(2026-05-24,M11 Pre-flight 增强版):用 created_at 代替 started_at
  //
  // 历史 Bug:用户多次反馈"已运行 0s"刷新页面后重新计时。
  // 后端虽用 SQL COALESCE 兜底("started_at IS NULL 才写入"),但只保护"新 sim";
  // 已被错误覆盖过的旧 sim,DB 里的 started_at 就是"被覆盖后"的时间,后端没法救。
  // 此外 SSE 重连 / fetcher 拿到的 sim 快照偶尔也有过时 started_at。
  //
  // 真正治本:**改用 sim.created_at 作为锚点**。
  // 理由:
  //   - created_at 是 sim 创建时 INSERT 一次性写入,grep 全代码库没有任何
  //     UPDATE simulations SET created_at=? 的路径 — 绝对不会被覆盖
  //   - 而 started_at 在 _update_state(started_at=...) 历史代码里被多处写过
  //   - 用户感知的"已运行"自然包括 queued 等待(他从点击那一刻就开始等了),
  //     用 created_at 反而比 started_at 更符合用户预期
  //
  // 普适性:适用任何 sim,无需 localStorage,无需迁移老数据,
  // 已损坏的 sim 也能立刻显示正确 elapsed(因为 created_at 字段从未被损坏)。
  const elapsedSeconds = computed<number | null>(() => {
    const sim = simulation.value;
    if (!sim) return null;

    // 优先 created_at(绝对稳定),fallback started_at(防极端边界)
    const anchor = sim.created_at || sim.started_at;
    if (!anchor) return null;

    if (
      sim.state === "done"
      || sim.state === "failed"
      || sim.state === "cancelled"
    ) {
      // 终态:用 completed_at - anchor(若有)
      if (sim.completed_at) {
        const start = new Date(anchor).getTime();
        const end = new Date(sim.completed_at).getTime();
        return Math.max(0, Math.round((end - start) / 1000));
      }
      return null;
    }
    // 进行中:用当前时间 - anchor(_nowTick 每秒更新)
    const start = new Date(anchor).getTime();
    return Math.max(0, Math.round((_nowTick.value - start) / 1000));
  });

  const elapsedLabel = computed<string>(() => {
    const sec = elapsedSeconds.value;
    if (sec === null) return "";
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  });

  // Sprint 6.A2 polish(2026-05-22)修计时 bug:_startTimer 之前只在 pollFullDetail 调,
  // SSE 流接管时永远不启动 → elapsed 一直停在初始 _nowTick 与 started_at 的差(~1s)。
  // 改用 watch 跟踪 sim.state,running 三态启动 timer,其他状态停 — 不依赖 SSE/poll 路径
  watch(
    () => simulation.value?.state,
    (state) => {
      if (state === "queued" || state === "directing" || state === "composing") {
        _startTimer();
      } else {
        _stopTimer();
      }
    },
    { immediate: true },
  );

  /** 当前细化阶段(最近 SSE 事件的可读描述)— 让用户知道"AI 在做什么"。 */
  const detailedStageLabel = computed<string>(() => {
    const sim = simulation.value;
    if (!sim) return "";
    if (sim.state !== "directing") return "";
    if (events.value.length === 0) return "";

    // 找最近一条"幕内推进"事件
    for (let i = events.value.length - 1; i >= 0; i--) {
      const ev = events.value[i];
      switch (ev.kind) {
        case "evolution_scene_picked":
          return `选定场景:${ev.scene_name ?? ""}`;
        case "evolution_agents_summoned":
          return "召集本幕在场角色";
        case "evolution_agent_start":
          return `${ev.speaker ?? ""} 思考中…`;
        case "evolution_agent_done":
          return `${ev.speaker ?? ""} 发声完成`;
        case "evolution_rag_retrieved":
          return "RAG 召回原著相关片段…";
        case "evolution_narrator_sample":
          return `narrator 合稿候选 ${(ev.sample_index ?? 0) + 1}/3`;
        case "evolution_narrator_selected":
          return "narrator 候选择优完成";
        case "evolution_narrator_retried":
          return "narrator 一致性自检触发重生…";
        case "evolution_consistency_checked":
          return "一致性自检中…";
        case "evolution_world_facts_extracted":
          return "抽取世界事实账本…";
        case "evolution_plot_threads_tracked":
          return "更新主线追踪…";
        case "evolution_entities_registered":
          return "锁定实体身份…";
        case "evolution_actions_extracted":
          return "记录原子动作流水…";
        case "evolution_emotional_states_tracked":
          return "更新角色情绪链…";
        case "evolution_scene_done":
          return `第 ${(ev.scene_index ?? 0) + 1} 幕完成 ✓`;
      }
    }
    return "";
  });

  // Sprint 3.A 修:stageLabel 是阶段进度描述(供 dock subtitle / Detail view 用),
  // 跨 4 态共用。"推演完成"/"推演失败" 这种 mode-specific 措辞改为中性"已完成"/"已失败",
  // mode 专属动词(推演/重塑/续写/长篇)由调用方在标题上拼(SimulationDock.dockHeaderConfig)。
  // "AI 正在编排…" 和 "AI 正在编织…" 是技术阶段描述(director / composer 角色名),
  // 跨 mode 通用,保留。
  // Sprint 6.A2 M3.D-fix7(2026-05-19):evolution 模式用"幕",quick 模式用"轮"。
  const stageLabel = computed(() => {
    const sim = simulation.value;
    if (!sim) return "";
    const isEvolution = sim.mode === "evolution";
    const unit = isEvolution ? "幕" : "轮";
    switch (sim.state) {
      case "queued":
        return "正在排队";
      case "directing":
        if (isEvolution) {
          return `AI 正在演第 ${sim.current_round + 1} / ${sim.rounds_planned} ${unit}`;
        }
        return `AI 正在编排第 ${sim.current_round + 1} / ${sim.rounds_planned} ${unit}`;
      case "composing":
        return isEvolution
          ? "AI 正在合稿最终叙事"
          : "AI 正在编织最终叙事";
      case "done":
        return "已完成";
      case "failed":
        return "已失败";
      case "cancelled":
        return "已取消";
      default:
        return "";
    }
  });

  // ============================================================
  // 事件接收 + simulation 字段增量更新
  // ============================================================

  function pushEvent(ev: SimulationEvent) {
    ev._ts = Date.now();
    events.value.push(ev);
    if (events.value.length > MAX_EVENTS_KEPT) {
      events.value.splice(0, events.value.length - MAX_EVENTS_KEPT);
    }
  }

  function applyEventToSimulation(ev: SimulationEvent) {
    const sim = simulation.value;
    if (!sim) return;

    // 状态机推进
    if (ev.kind === "state_change" && ev.state) {
      sim.state = ev.state as SimulationState;
    } else if (ev.kind === "composing_start") {
      sim.state = "composing";
    } else if (ev.kind === "done") {
      sim.state = "done";
      if (ev.narrative) sim.narrative = ev.narrative;
      if (ev.cost_yuan !== undefined) sim.cost_yuan = ev.cost_yuan;
      phase.value = "done";
    } else if (ev.kind === "error") {
      sim.state = "failed";
      sim.error_message = ev.message ?? "已失败,无具体错误信息";
      phase.value = "failed";
      errorMessage.value = sim.error_message;
    }

    // 进度推进(quick:round_done;evolution:scene_done — 都更 current_round)
    if (ev.kind === "round_done" && ev.round !== undefined) {
      sim.current_round = ev.round;
    }
    if (ev.kind === "evolution_scene_done" && ev.scene_index !== undefined) {
      sim.current_round = ev.scene_index + 1;
    }
    if (ev.cost_yuan !== undefined) {
      sim.cost_yuan = ev.cost_yuan;
    }
  }

  // ============================================================
  // SSE 连接 + fallback polling
  // ============================================================

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (fallbackTimer !== null) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }
    // M6-fix5:closeStream 时也停 timer(SSE 关 = 通常已 done/error)
    _stopTimer();
  }

  async function pollFullDetail(simId: string): Promise<SimulationFull | null> {
    try {
      const resp = await api.get<SimulationFull>(`/simulations/${simId}`);
      simulation.value = resp;
      // M6-fix5:启停 elapsed timer
      if (
        resp.state === "directing"
        || resp.state === "composing"
        || resp.state === "queued"
      ) {
        _startTimer();
      } else {
        _stopTimer();
      }
      return resp;
    } catch (e) {
      phase.value = "error";
      errorMessage.value =
        e instanceof ApiError
          ? `读取推演详情失败:${e.message}`
          : "网络异常,无法获取推演详情";
      return null;
    }
  }

  /** SSE 失败 / 不可用时降级:2s 轮询完整详情 */
  function startFallbackPolling(simId: string) {
    if (fallbackTimer !== null) return;
    fallbackTimer = setInterval(async () => {
      const sim = await pollFullDetail(simId);
      if (!sim) return;
      if (sim.state === "done") {
        phase.value = "done";
        closeStream();
      } else if (sim.state === "failed") {
        phase.value = "failed";
        errorMessage.value = sim.error_message ?? "已失败";
        closeStream();
      } else if (sim.state === "cancelled") {
        phase.value = "failed";
        errorMessage.value = "已取消";
        closeStream();
      }
    }, FALLBACK_POLL_INTERVAL_MS);
  }

  async function openStream(simId: string) {
    closeStream();
    events.value = [];

    // 1. 拿短期 SSE token
    let sseToken: string;
    try {
      const tokResp = await api.post<{ token: string; expires_in: number }>(
        "/auth/sse_token",
      );
      sseToken = tokResp.token;
    } catch {
      // 拿不到 SSE token → 直接降级 polling
      if (import.meta.env.DEV) {
        console.warn("[useSimulation] SSE token 失败,降级 polling");
      }
      startFallbackPolling(simId);
      return;
    }

    // 2. 创建 EventSource
    // ⚠️ 必须带 VITE_API_BASE 前缀:拆子域部署时前端在 app.子域,裸相对 /api 会打到
    // app 子域(无后端 → 兜底 index.html → SSE 挂)。同 client.ts / useExtractJob 口径。
    const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) || "";
    const url = `${apiBase}/api/simulations/${simId}/stream?token=${encodeURIComponent(
      sseToken,
    )}`;
    const es = new EventSource(url);
    eventSource = es;

    es.onmessage = (e) => {
      let payload: SimulationEvent;
      try {
        payload = JSON.parse(e.data);
      } catch {
        return;
      }
      pushEvent(payload);
      applyEventToSimulation(payload);

      // 终态:关连接;done 时补拉一次完整详情(snapshot 不带 timeline)
      if (
        payload.kind === "done" ||
        payload.kind === "error" ||
        payload.kind === "cancelled"
      ) {
        closeStream();
        if (payload.kind === "done") {
          void pollFullDetail(simId);
        }
      }
    };

    es.onerror = () => {
      // SSE 失败可能是网络抖动 / 后端重启 / 鉴权过期。降级 polling 兜底
      closeStream();
      if (import.meta.env.DEV) {
        console.warn("[useSimulation] SSE 异常,降级 polling");
      }
      startFallbackPolling(simId);
    };
  }

  // ============================================================
  // 主流程
  // ============================================================

  function openConfiguring() {
    closeStream();
    phase.value = "configuring";
    simulation.value = null;
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    events.value = [];
  }

  /** 内部:已知 sim_id → 拉完整详情 + 视情况开 SSE */
  async function _fetchAndStream(simId: string) {
    const sim = await pollFullDetail(simId);
    if (!sim) return;

    if (sim.state === "done") {
      phase.value = "done";
      return;
    }
    if (sim.state === "failed") {
      phase.value = "failed";
      errorMessage.value = sim.error_message ?? "已失败";
      return;
    }
    if (sim.state === "cancelled") {
      phase.value = "failed";
      errorMessage.value = "已取消";
      return;
    }

    // 非终态:开 SSE
    phase.value = "running";
    await openStream(simId);
  }

  /**
   * POST 创建推演 → 拉详情 → 开 SSE。
   * 任何错误 → phase = "error" + errorMessage,**不进 running**。
   * QUOTA_EXCEEDED 特殊处理:errorCode + errorDetail 暴露给父组件。
   */
  async function start(params: CreateSimulationRequest) {
    phase.value = "creating";
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    events.value = [];

    try {
      const resp = await api.post<SimulationCreatedResponse>(
        `/projects/${getProjectId()}/simulations`,
        params,
      );
      // INS-A3(2026-05-27):埋点 simulation_create
      // 异步导入避免循环依赖,失败静默(useAnalytics 内部已 try/catch)
      import("./useAnalytics").then(({ track }) => {
        track("simulation_create", {
          project_id: getProjectId(),
          simulation_id: resp.simulation_id,
          meta: {
            mode: params.mode ?? null,
            target_chars: params.target_chars ?? null,
          },
        });
      });
      // 2026-06-01:广播 sim:created 事件 → 项目内 SimulationsListPanel 监听后实时新增卡片
      // 异步 import 避免顶层 pinia 循环依赖
      import("../stores/events").then(({ useEventBus }) => {
        try {
          useEventBus().emit("sim:created", {
            sim_id: resp.simulation_id,
            project_id: getProjectId(),
          });
        } catch (err) {
          if (import.meta.env.DEV) {
            // eslint-disable-next-line no-console
            console.warn("[useSimulation] emit sim:created failed:", err);
          }
        }
      });
      await _fetchAndStream(resp.simulation_id);
    } catch (e) {
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorDetail.value = e.detail;
        // Sprint C.1:QUOTA_EXCEEDED(资源容量类) + INSUFFICIENT_CREDITS(AI credit 不足)
        // 都走 UpgradeModal 兜底,message 直接用后端文案
        if (
          e.code === "QUOTA_EXCEEDED" ||
          e.code === "INSUFFICIENT_CREDITS"
        ) {
          errorMessage.value = e.message;
        } else if (e.code === "TOO_FEW_CHARACTERS") {
          errorMessage.value = "至少需要 3 个角色才能续写";
        } else {
          errorMessage.value = e.message;
        }
      } else {
        errorMessage.value = "创建失败,请稍后重试";
      }
    }
  }

  /**
   * 已知 sim_id 直接订阅 — Sprint 1.J 我的剧情线 / 详情页用。
   * 直接 _fetchAndStream,不 POST。
   */
  async function subscribe(simId: string) {
    closeStream();
    // P-6 修复(2026-05-23):stale-while-revalidate — 不立刻清 simulation.value,
    // 保留旧数据无缝替换,直到 pollFullDetail 拉来新 sim 数据覆盖。原行为切 sim 时
    // 整块 v-if 闪一下,视觉断层。events 是本 sim 实时事件,必须清。
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    events.value = [];
    await _fetchAndStream(simId);
  }

  /**
   * 断点续推(Sprint 1.P)— failed / 中断的 sim 接着第 N+1 轮跑。
   * POST /simulations/{id}/resume → 后端 reset state → kick_off → 复用 _fetchAndStream
   * 不扣额外 continuation 配额。
   *
   * 错误码:
   *   SIMULATION_STILL_RUNNING  409 sim 已在跑(双 resume 防护)
   *   SIMULATION_NOT_RESUMABLE  422 done / queued / 无进度
   */
  async function resume(simId: string) {
    closeStream();
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    events.value = [];
    phase.value = "creating";   // 视觉同 start;成功后切 running
    try {
      await api.post<SimulationCreatedResponse>(
        `/simulations/${simId}/resume`,
        {},
      );
      await _fetchAndStream(simId);
    } catch (e) {
      phase.value = "error";
      if (e instanceof ApiError) {
        errorCode.value = e.code;
        errorDetail.value = e.detail;
        if (e.code === "SIMULATION_STILL_RUNNING") {
          errorMessage.value = "AI 创作已在后台运行,稍等几秒后刷新页面";
        } else if (e.code === "SIMULATION_NOT_RESUMABLE") {
          errorMessage.value = "这条推演无法恢复(已完成 / 无进度)";
        } else {
          errorMessage.value = e.message;
        }
      } else {
        errorMessage.value = "恢复失败,请稍后重试";
      }
    }
  }

  function reset() {
    closeStream();
    phase.value = "idle";
    simulation.value = null;
    errorMessage.value = null;
    errorCode.value = null;
    errorDetail.value = null;
    events.value = [];
  }

  return {
    // state
    phase,
    simulation,
    errorMessage,
    errorCode,
    errorDetail,
    events,
    // derived
    progress,
    stageLabel,
    // M6-fix5:细粒度进度感
    detailedStageLabel,
    elapsedLabel,
    elapsedSeconds,
    // actions
    openConfiguring,
    start,
    subscribe,
    resume,
    reset,
  };
}
