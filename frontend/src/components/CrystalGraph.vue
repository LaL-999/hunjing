<script setup lang="ts">
/**
 * CrystalGraph.vue — 浑晶 3D 图谱核心组件
 *
 * Day 1: 基础 3D 渲染 + 自定义节点/halo
 * Day 2: 冷启动优化 + 真实数据接入
 * Day 3: 暗化模式 + 高亮搏动 — 仿真启动时图谱整体暗下,
 *         participants 列表里的角色节点保持高亮 + 1.6s 周期搏动
 */

import { ref, watch, onMounted, onBeforeUnmount, computed } from "vue";
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import * as THREE from "three";

import type {
  CharacterTone,
  ForceGraphData,
  GraphLink,
  GraphNode,
  RelationType,
} from "../types/graph";
import {
  hashColorForType,
  loadThemeColors,
  type ThemeColors,
} from "../composables/useGraphTheme";

const props = defineProps<{
  data: ForceGraphData;
  /** 滚轮缩放灵敏度系数,1.0 是默认 */
  zoomSensitivity?: number;
  /** 仿真启动后要高亮 + 搏动的节点 name 列表 */
  participants?: string[];
  /** dim 模式开关:启动后所有非 participant 节点变暗 */
  dimMode?: boolean;
  /**
   * 1.M.2:AI 对焦完成后这些 PERSON 节点开始短暂脉搏(由父组件 6 秒后清空)。
   * 与 participant pulse 独立运作:不依赖 dimMode,纯视觉回声;
   * 脉搏更快(0.8s 周期),让"被 AI 触摸过"的反馈鲜明。
   */
  refinedNodeIds?: string[];
  /**
   * 1.M.3:推演刚 done 的"光迹"节点 — 稳态 emissive + halo 提升,不脉搏。
   * 与 refined pulse 区分(后者是"刚被改",光迹是"刚演过");
   * 可与 simulation pulse 共存(优先级:simulation > refined > trail)。
   * 父组件控制清空时机(默认 navigate 离开为止,或开新 sim 时复位)。
   */
  trailNodeIds?: string[];
  /**
   * Sprint 2.C:反事实变量影响范围(BFS 触达的节点 ids)。
   * 父组件在 SimulationDock open 或 CounterfactualWorkbench open 时传入,
   * 让用户视觉看到"当前 reshape % 下推演会聚焦的节点群"。
   * 视觉:halo opacity 0.35(默认 0.12)+ scale 1.15;退出时还原。
   * 与 simulation pulse 不冲突(pulse 临时覆盖,pulse 结束 baseline 仍是 affected)。
   */
  affectedNodeIds?: string[];
  /**
   * Sprint D.2.A:沙盘模式开关 — 形态 D 入口
   * 默认 false 走形态 A/B/C 现有行为;打开 = 形态 D:
   *   - counterfactualSourceIds 内的节点显类型颜色的光晕环(紫=角色 / 蓝=事件)
   *   - 默认 halo opacity 不动(0.12),光晕环是**额外**的 mesh
   * 关闭沙盘 → 光晕环淡出还原。
   */
  sandboxMode?: boolean;
  /**
   * Sprint D.2.A:反事实源节点 ids 按 type 分桶 — 沙盘模式渲染光晕环用
   *   character → 紫色 halo(避开 accent 紫,用更亮的紫罗兰)
   *   event     → 蓝色 halo
   * 世界观反事实(target_id='_global_')不在此(无节点),由父组件顶栏 chip 兜底显示。
   * 关系反事实本 sprint 不参与(关系是边不是节点)。
   */
  counterfactualSourceIds?: {
    character: string[];
    event: string[];
  };
  /**
   * Sprint D.2.B fix:关系强度显示阈值(10-80,projects.graph_strength_threshold)。
   * **Runtime 决定 link 显隐,不动 graphData** — 改这个 prop 不触发 force-graph
   * 重建 line,避免阈值变化时线条闪一帧非玻璃态(根因:force-graph 创建 line 时
   * 用 default opacity,几帧后才走 accessor,中间有视觉空隙)。
   * dynamicLinkOpacity 内部读此 prop:link.strengthScore < threshold → opacity=0(隐藏)。
   */
  graphStrengthThreshold?: number;
}>();

const emit = defineEmits<{
  (e: "node-click", node: GraphNode): void;
  (e: "link-click", link: GraphLink): void;
  (e: "fps-update", fps: number): void;
  (e: "simulation-state", isSimulating: boolean): void;
  // Sprint 1.M.1.C:节点拖动结束 → 父组件 PATCH 持久化 position
  (e: "node-dragend", node: GraphNode, position: { x: number; y: number; z: number }): void;
  // Sprint 1.M.1.E:Shift + 点节点 = 关系连接(分两次,父组件管 source / target)
  (e: "node-shift-click", node: GraphNode): void;
  // Sprint 1.M.1.D:右键空白 = 弹"新建"菜单(传屏幕坐标给父组件定位 menu)
  (e: "background-rclick", position: { x: number; y: number }): void;
  // Sprint 1.M.3:Ctrl/Cmd + 点节点 = 框选(切换该节点入/出 frame 集合,父组件管多选 state)
  (e: "node-frame-click", node: GraphNode): void;
  // 2026-05 P2:hover 节点 → 父组件渲染浮窗(node=null 表示离开)
  // screenPos:浮窗定位用的鼠标客户端坐标
  (e: "node-hover", node: GraphNode | null, screenPos: { x: number; y: number } | null): void;
}>();

const containerRef = ref<HTMLDivElement | null>(null);
let graph: ForceGraph3DInstance | null = null;
let theme: ThemeColors | null = null;

// 节点 id → mesh 引用,用于动态修改材质(dim/highlight)
const nodeCoreMeshes = new Map<string, THREE.Mesh>();
const nodeHaloMeshes = new Map<string, THREE.Mesh>();

// Sprint D.2.A — 反事实光晕环 mesh(圆环,土星环式)。
//   sandboxMode + node id 在 counterfactualSourceIds.character/event 中时显
//   颜色编码:character=紫罗兰 / event=蓝
//   未激活时 opacity=0(隐藏),保留 mesh 在 group 里避免每次重建
const nodeCfHaloMeshes = new Map<string, THREE.Mesh>();

/** 反事实类型颜色编码(避开 accent #7C3AED 紫,用更亮的紫罗兰让对比清晰)*/
const CF_HALO_COLOR_CHARACTER = 0xC084FC;   // 紫罗兰
const CF_HALO_COLOR_EVENT = 0x3B82F6;       // 蓝
// D.2.A polish 1:从 0.65 → 0.95(用户反馈"开沙盘没视觉差异",需要更显眼)
const CF_HALO_OPACITY_ACTIVE = 0.95;

// D.2.A polish 2 — 节点 core mesh 玻璃态(沙盘模式 baseline,用户拍板:
//   "开启沙盘之后所有节点 + 线条切换成玻璃态,既不明显也不完全消失")
// 让反事实光晕环成视觉主角,原节点 / 线条褪成磨砂玻璃感
// 2026-06-08 UI 大升级:emissive 从 0.5 → 0.22(降"过曝发光",节点更克制
// 配合 tokens.css 新降饱和的 --hj-* 色,整体观感从"游戏霓虹" → "水墨浑晶")
const NODE_OPACITY_DEFAULT = 0.88;
const NODE_OPACITY_SANDBOX = 0.26;
const NODE_EMISSIVE_DEFAULT = 0.22;
const NODE_EMISSIVE_SANDBOX = 0.10;

// D.2.B — BFS 影响波纹动画
const RIPPLE_MAX_HOPS = 4;              // BFS 跳数上限(对应 reshape 50% 中等扩散)
const RIPPLE_PER_DEPTH_DELAY_MS = 100;  // 每层延迟(涟漪感)
const RIPPLE_DURATION_MS = 800;         // 单节点波纹时长(ease-in-out)
const RIPPLE_PEAK_HALO_OPACITY = 0.65;
const RIPPLE_PEAK_HALO_SCALE = 1.4;

// participants 计算:用 Set 提速,linkOpacity 每帧调用
const participantsSet = computed(() => new Set(props.participants ?? []));

// FPS 监控
let fpsFrames = 0;
let fpsLast = performance.now();
let fpsRaf: number | null = null;

// 2026-06-02 hotfix:resize listener 清理(治内存泄漏 — 原 init() 内 addEventListener 无对应 remove)
let resizeListenerCleanup: (() => void) | null = null;

// 搏动动画 RAF(participant pulse — simulation 时连续)
let pulseRaf: number | null = null;
// 1.M.2:refined pulse RAF(AI 对焦完后短暂脉搏,独立于 participant)
let refinedPulseRaf: number | null = null;
// refined 节点原始 emissive / scale 备份,脉搏停时还原
const refinedBaseline = new Map<string, { emissive: number; scale: number; haloOpacity: number }>();

// 2026-05 P5:远距 halo 隐藏 LOD — 摄像机拉远到看全局时,halo 会糊成一片
//   反而妨碍辨识节点 + 增加 GPU 渲染负担。隐藏 halo 后远视图清爽 + GPU 解压。
const HALO_HIDE_DISTANCE = 350;   // 默认初始相机 z=220;>350 算远视图
let lastHaloVisible = true;

function tickFps() {
  fpsFrames++;
  const now = performance.now();
  if (now - fpsLast >= 1000) {
    emit("fps-update", fpsFrames);
    fpsFrames = 0;
    fpsLast = now;
    // 每秒顺手检测摄像机距离,触发 halo LOD(不另起 raf)
    if (graph) {
      const cam = graph.camera();
      const dist = Math.hypot(cam.position.x, cam.position.y, cam.position.z);
      const shouldShow = dist < HALO_HIDE_DISTANCE;
      if (shouldShow !== lastHaloVisible) {
        for (const halo of nodeHaloMeshes.values()) {
          halo.visible = shouldShow;
        }
        lastHaloVisible = shouldShow;
      }
    }
  }
  fpsRaf = requestAnimationFrame(tickFps);
}

function nodeColor(node: GraphNode, t: ThemeColors): string {
  if (node.type === "PERSON" && node.tone) {
    return t.characterTone[node.tone as CharacterTone];
  }
  return t.typeColor[node.type] || t.crystalLight;
}

function nodeSize(node: GraphNode): number {
  if (node.type === "EVENT") return 0.825;
  if (node.type === "PERSON") return 1.35;
  return 1.05;
}

function makeNodeObject(node: GraphNode, t: ThemeColors): THREE.Object3D {
  const color = new THREE.Color(nodeColor(node, t));
  const size = nodeSize(node);

  const geo =
    node.type === "EVENT"
      ? new THREE.OctahedronGeometry(size, 0)
      : new THREE.IcosahedronGeometry(size, 0);

  // D.2.A polish 2:沙盘模式 baseline 是玻璃态(0.28/0.18);默认浓重(0.92/0.5)
  const inSandbox = props.sandboxMode === true;
  const mat = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: inSandbox ? NODE_EMISSIVE_SANDBOX : NODE_EMISSIVE_DEFAULT,
    metalness: 0.2,
    roughness: 0.4,
    transparent: true,
    opacity: inSandbox ? NODE_OPACITY_SANDBOX : NODE_OPACITY_DEFAULT,
  });
  const mesh = new THREE.Mesh(geo, mat);

  const haloGeo = new THREE.IcosahedronGeometry(size * 1.6, 0);
  const haloMat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: 0,                 // 冷启动期间隐藏
    side: THREE.BackSide,
    depthWrite: false,
  });
  const halo = new THREE.Mesh(haloGeo, haloMat);

  // 注册到 Map,后续 dim/pulse / sandbox 都可以拿到引用
  nodeCoreMeshes.set(node.id, mesh);
  nodeHaloMeshes.set(node.id, halo);
  // D.2.B:记 halo 原色,波纹临时染色后还原用
  nodeHaloOriginalColor.set(node.id, color.getHex());

  const group = new THREE.Group();
  group.add(mesh);
  group.add(halo);
  // Sprint D.7 修复:cfHalo Torus 不再无条件创建(原 D.2.A 实现每节点必建)
  //   省 ~96 顶点 × 400 节点 = 38K 顶点 GPU 数据(降级分段 6×16 后)
  //   沙盘开 + 节点是反事实源时,applyCounterfactualHalos lazy 创建
  //   存 cfHaloRadius 让 ensureCfHalo 后续按尺寸创建 Torus
  group.userData.cfHaloRadius = size * 2.2;
  return group;
}

function linkColor(link: GraphLink, t: ThemeColors): string {
  // M7.H(2026-05-20):预设 11 种走 theme.relation 主色;自定义 / LLM 自创关系
  // (如"暗恋""革命战友""忘年交")→ hashColorForType 派生稳定 HSL 色
  // 同 type 永远同色;视觉舒适(S=55% L=65%,deep-space 背景下可见)
  const presetColor = t.relation[link.type as RelationType];
  return presetColor || hashColorForType(link.type);
}

/**
 * Sprint D.7 fix:阈值机制从"绝对 score"切换到"rank-based 百分位"。
 *
 * 原机制问题:LLM 抽取的关系大多落在 moderate(score=50)档,阈值越过 50 时
 * 关系数断崖式消失(60% 几乎全黑屏)。用户期望"阈值 X% = 隐藏 X% 弱关系",
 * 0=全显 / 100=全隐,平滑过渡。
 *
 * 新算法:
 *   - 把所有 link 按 strengthScore 升序排稳定 rank(0..N-1,score 同则按 ref 出现序)
 *   - 阈值 X% → 隐藏 rank < ceil(X/100 * N) 的 link
 *   - 边界:0% → 全显(cutoff=0,任何 rank 都 ≥ 0,不命中隐藏);100% → cutoff=N,全隐
 *   - 保留"弱在先、强在后"语义:rank 低 = score 低 = 弱关系,优先被隐藏
 */
const linkRankCutoff = computed<number>(() => {
  const links = props.data?.links ?? [];
  const threshold = props.graphStrengthThreshold ?? 0;
  if (links.length === 0) return 0;
  if (threshold <= 0) return 0;             // 全显
  if (threshold >= 100) return links.length; // 全隐
  return Math.ceil((threshold / 100) * links.length);
});

/** link 引用 → rank 映射;props.data 变化才重算。
 *  用 Map<linkRef, rank> 而不是 stringId,避免每帧拼字符串。force-graph 不 clone link
 *  对象引用,所以传给 linkOpacity accessor 的 link 跟 props.data.links 里的是同一引用。 */
const linkRanks = computed<Map<GraphLink, number>>(() => {
  const links = props.data?.links ?? [];
  // 稳定排序:score 升序;score 相同按数组中的原始位置(浏览器 Array.sort 稳定排序保证)
  const sorted = links
    .map((l, i) => ({ link: l, score: l.strengthScore ?? 50, idx: i }))
    .sort((a, b) => a.score - b.score || a.idx - b.idx);
  const ranks = new Map<GraphLink, number>();
  sorted.forEach((entry, rank) => ranks.set(entry.link, rank));
  return ranks;
});

/**
 * 动态 linkOpacity 决策树:
 *   1. **rank-based 阈值**(D.7 fix):link 的 rank < cutoff → 0(隐藏,弱关系优先)
 *      原本在 useProjectGraph 过滤 link 数组,改成 runtime opacity 隐藏 —
 *      force-graph link 数不变,不重建,无闪烁
 *   2. **sandboxMode**(D.2.A polish 2)→ 0.15 玻璃态(让反事实光晕环成视觉主角)
 *   3. **dimMode** → 仅两端 participant 的关系亮(0.6),其余 0.05
 *   4. 普通态 → 0.55 默认
 */
function dynamicLinkOpacity(link: GraphLink): number {
  const cutoff = linkRankCutoff.value;
  if (cutoff > 0) {
    const rank = linkRanks.value.get(link);
    // rank 拿不到(理论上不发生,只在过渡帧 props.data 已变但 force-graph 还在 render
    // 旧 link)按 Infinity 处理偏显示侧 — 过渡帧多显示一刹比闪一下黑帧更友好
    if (rank !== undefined && rank < cutoff) return 0;
  }
  if (props.sandboxMode) return 0.15;
  if (!props.dimMode) return 0.55;
  const src = typeof link.source === "string" ? link.source : link.source.id;
  const tgt = typeof link.target === "string" ? link.target : link.target.id;
  const set = participantsSet.value;
  return set.has(src) && set.has(tgt) ? 0.6 : 0.05;
}

/** 冷启动 onEngineStop 后渐显所有 halo */
function fadeInHalos() {
  const targetOpacity = 0.12;
  const duration = 400;
  const start = performance.now();

  function step(now: number) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    const opacity = targetOpacity * eased;
    for (const halo of nodeHaloMeshes.values()) {
      const m = halo.material as THREE.MeshBasicMaterial;
      m.opacity = opacity;
    }
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/**
 * 应用 dim 模式 — Day 3 v2:对比度大幅增强
 *
 * 原 v1 dim 系数太温和(opacity 0.42、emissive 0.08),用户反馈"看不出来"。
 * 根因是反差不够 + ch74 一半节点都是 participant,负空间不够。
 * v2 做三件事让对比度成倍放大:
 *   ① non-participant: opacity 0.15(几乎看不见)、emissive 0.0、halo 0、缩小到 0.6 倍
 *   ② participant: opacity 1.0、emissive 1.0、halo 0.5、放大到 1.25 倍
 *   ③ pulse 不光改 emissive,还同步缩放节点(1.2 ↔ 1.35),搏动可视化为体积变化
 */
function applyDimMode(dim: boolean) {
  const set = participantsSet.value;

  for (const [id, core] of nodeCoreMeshes) {
    const mat = core.material as THREE.MeshStandardMaterial;
    const halo = nodeHaloMeshes.get(id);
    const haloMat = halo?.material as THREE.MeshBasicMaterial | undefined;
    const group = core.parent as THREE.Group | null;

    if (!dim) {
      mat.emissiveIntensity = 0.5;
      mat.opacity = 0.92;
      if (haloMat) haloMat.opacity = 0.12;
      if (group) group.scale.setScalar(1.0);
      continue;
    }

    if (set.has(id)) {
      // 高亮 + 放大,后续 pulseRaf 会再叠加搏动效果
      mat.emissiveIntensity = 1.0;
      mat.opacity = 1.0;
      if (haloMat) haloMat.opacity = 0.5;
      if (group) group.scale.setScalar(1.25);
    } else {
      // 暗化 + 缩小到 0.4 倍(Day 3 v3 加力,几乎缩成一个点)
      mat.emissiveIntensity = 0.0;
      mat.opacity = 0.15;
      if (haloMat) haloMat.opacity = 0;
      if (group) group.scale.setScalar(0.4);
    }
  }

  // 相机推近 / 拉远的逻辑已搬到下面单独 watch(props.dimMode),避免 participants
  // 变化时也意外触发相机动画(那会覆盖 OrbitControls 的旋转角度,导致图谱被"拉直"
  // 后无法响应拖拽)。

  if (dim && set.size > 0) startPulse();
  else stopPulse();
}

/**
 * 沿当前 camera→target 方向 dolly 相机到目标距离,保留旋转角度。
 *
 * 不能直接 graph.cameraPosition({0,0,z}),那会把相机生硬拉回正面,
 * 用户已经旋转过的图谱视角全部丢失。
 */
function dollyCameraTo(distance: number, transitionMs: number) {
  if (!graph) return;
  const camera = graph.camera() as THREE.PerspectiveCamera;
  const controls = graph.controls() as { target?: THREE.Vector3 } | undefined;
  const target = controls?.target ?? new THREE.Vector3(0, 0, 0);

  const direction = new THREE.Vector3()
    .subVectors(camera.position, target)
    .normalize();
  const newPos = direction.multiplyScalar(distance).add(target);

  graph.cameraPosition(
    { x: newPos.x, y: newPos.y, z: newPos.z },
    { x: target.x, y: target.y, z: target.z },   // 显式 lookAt,不要 undefined
    transitionMs,
  );
}

function startPulse() {
  if (pulseRaf !== null) return;
  const set = participantsSet.value;

  function tick(now: number) {
    // 1.6 秒一个完整周期(2π / 1600 ≈ 0.0039)
    const wave = (Math.sin(now * 0.0039) + 1) / 2; // 0..1
    for (const id of set) {
      const core = nodeCoreMeshes.get(id);
      if (!core) continue;
      const mat = core.material as THREE.MeshStandardMaterial;
      const halo = nodeHaloMeshes.get(id);
      const group = core.parent as THREE.Group | null;

      // 三轨同步搏动:emissive(亮度)+ halo(光晕)+ scale(体积)
      mat.emissiveIntensity = 0.6 + wave * 1.0;       // 0.6..1.6
      if (halo) {
        const haloMat = halo.material as THREE.MeshBasicMaterial;
        haloMat.opacity = 0.35 + wave * 0.35;         // 0.35..0.7
      }
      if (group) {
        group.scale.setScalar(1.2 + wave * 0.15);     // 1.2..1.35
      }
    }
    pulseRaf = requestAnimationFrame(tick);
  }
  pulseRaf = requestAnimationFrame(tick);
}

function stopPulse() {
  if (pulseRaf !== null) {
    cancelAnimationFrame(pulseRaf);
    pulseRaf = null;
  }
}

// ============================================================
// 1.M.2:refined pulse — AI 对焦完成的角色节点短暂回声脉搏
// 与 participant pulse 完全独立,可与 simulation 共存(不抢 emissive 写入,
// 各自只动自己的目标节点子集)
// ============================================================

function startRefinedPulse() {
  if (refinedPulseRaf !== null) return;
  const ids = props.refinedNodeIds ?? [];
  if (ids.length === 0) return;

  // 备份原始 emissive / scale / halo opacity,停时还原(避免污染 dim 状态)
  refinedBaseline.clear();
  for (const id of ids) {
    const core = nodeCoreMeshes.get(id);
    const halo = nodeHaloMeshes.get(id);
    const group = core?.parent as THREE.Group | null;
    if (!core) continue;
    const mat = core.material as THREE.MeshStandardMaterial;
    const haloMat = halo?.material as THREE.MeshBasicMaterial | undefined;
    refinedBaseline.set(id, {
      emissive: mat.emissiveIntensity,
      scale: group?.scale.x ?? 1.0,
      haloOpacity: haloMat?.opacity ?? 0.12,
    });
  }

  function tick(now: number) {
    // 0.8s 周期(比 participant 的 1.6s 快一倍,鲜明区分"AI 触摸过"的回声)
    const wave = (Math.sin(now * 0.0078) + 1) / 2; // 0..1
    for (const id of ids) {
      const core = nodeCoreMeshes.get(id);
      if (!core) continue;
      const mat = core.material as THREE.MeshStandardMaterial;
      const halo = nodeHaloMeshes.get(id);
      const group = core.parent as THREE.Group | null;
      const baseline = refinedBaseline.get(id);
      if (!baseline) continue;

      // 三轨同步:emissive 强 + halo 亮 + scale 微涨
      mat.emissiveIntensity = baseline.emissive + 0.4 + wave * 0.8;   // base + 0.4..1.2
      if (halo) {
        const haloMat = halo.material as THREE.MeshBasicMaterial;
        haloMat.opacity = baseline.haloOpacity + 0.25 + wave * 0.35;  // base + 0.25..0.6
      }
      if (group) {
        group.scale.setScalar(baseline.scale * (1.0 + wave * 0.18));  // base..base*1.18
      }
    }
    refinedPulseRaf = requestAnimationFrame(tick);
  }
  refinedPulseRaf = requestAnimationFrame(tick);
}

function stopRefinedPulse() {
  if (refinedPulseRaf !== null) {
    cancelAnimationFrame(refinedPulseRaf);
    refinedPulseRaf = null;
  }
  // 还原 emissive / scale / halo,避免脉搏停后节点卡在亮态
  for (const [id, baseline] of refinedBaseline) {
    const core = nodeCoreMeshes.get(id);
    const halo = nodeHaloMeshes.get(id);
    const group = core?.parent as THREE.Group | null;
    if (!core) continue;
    const mat = core.material as THREE.MeshStandardMaterial;
    const haloMat = halo?.material as THREE.MeshBasicMaterial | undefined;
    mat.emissiveIntensity = baseline.emissive;
    if (haloMat) haloMat.opacity = baseline.haloOpacity;
    if (group) group.scale.setScalar(baseline.scale);
  }
  refinedBaseline.clear();
}

// ============================================================
// 1.M.3:trail glow — 稳态(无 RAF),sim done 后留光迹给参与角色
// 改动一次性:apply 时把 emissive / halo 提升;clear 时还原
// 与 dim/pulse 共存:dim 时 trail 不抢(dim 已经把 emissive 干到 0)
// ============================================================

const trailBaseline = new Map<string, { emissive: number; haloOpacity: number }>();

function applyTrailGlow(ids: readonly string[]) {
  // 先清旧 trail(若有),还原 baseline
  clearTrailGlow();
  if (ids.length === 0) return;
  // dim 模式时不叠 trail(避免和 dim 冲突,dim 是 simulation 状态优先级更高)
  if (props.dimMode) return;

  for (const id of ids) {
    const core = nodeCoreMeshes.get(id);
    if (!core) continue;
    const mat = core.material as THREE.MeshStandardMaterial;
    const halo = nodeHaloMeshes.get(id);
    const haloMat = halo?.material as THREE.MeshBasicMaterial | undefined;
    trailBaseline.set(id, {
      emissive: mat.emissiveIntensity,
      haloOpacity: haloMat?.opacity ?? 0.12,
    });
    mat.emissiveIntensity = mat.emissiveIntensity + 0.4;
    if (haloMat) haloMat.opacity = Math.min(1.0, (haloMat.opacity ?? 0.12) + 0.2);
  }
}

function clearTrailGlow() {
  for (const [id, baseline] of trailBaseline) {
    const core = nodeCoreMeshes.get(id);
    const halo = nodeHaloMeshes.get(id);
    if (!core) continue;
    const mat = core.material as THREE.MeshStandardMaterial;
    const haloMat = halo?.material as THREE.MeshBasicMaterial | undefined;
    mat.emissiveIntensity = baseline.emissive;
    if (haloMat) haloMat.opacity = baseline.haloOpacity;
  }
  trailBaseline.clear();
}

// 2026-05 P3:hover 时 pin 节点(防 force tick 让节点震动 → 第一次 click miss)
let pinnedHoverNode: (GraphNode & { fx?: number | null; fy?: number | null; fz?: number | null; x?: number; y?: number; z?: number }) | null = null;
// 2026-05 P2:鼠标位置追踪 — onNodeHover 不带鼠标坐标,自己记
let lastMouseX = 0;
let lastMouseY = 0;

function onContainerMouseMove(e: MouseEvent) {
  lastMouseX = e.clientX;
  lastMouseY = e.clientY;
}

function init() {
  if (!containerRef.value) return;
  theme = loadThemeColors();

  emit("simulation-state", true);

  // 2026-05 P2:监听 mousemove 让 onNodeHover 能给浮窗传定位坐标
  containerRef.value.addEventListener("mousemove", onContainerMouseMove);

  // 2026-05 P5:自适应 cooldown — 节点多时 simulation 更早静止,用户感觉"稳定更快"
  // 红楼梦 416 节点场景:80 ticks 跑满要 ~3s,大图 60 ticks ~1.5s 就够近似稳定
  const nodeCount = props.data.nodes.length;
  const adaptiveCooldownTicks = nodeCount > 200 ? 55 : nodeCount > 100 ? 70 : 80;
  const adaptiveCooldownTime = nodeCount > 200 ? 1500 : 2000;

  graph = new ForceGraph3D(containerRef.value)
    .backgroundColor(theme.deepSpace)
    .graphData(props.data as never)
    .nodeId("id")
    .nodeRelSize(2)
    // 2026-06-06:三方库 force-graph 的 NodeAccessor 期待 NodeObject 参数,
    // 我们运行时已知是 GraphNode。用 unknown 而非 never 让 TS 不强 narrowing 报错。
    .nodeThreeObject(((n: unknown) => makeNodeObject(n as GraphNode, theme!)) as never)
    .linkColor(((l: unknown) => linkColor(l as GraphLink, theme!)) as never)
    .linkOpacity(0.55)                              // 静态默认值
    .linkWidth(0.6)
    .linkDirectionalParticles(0)
    .cooldownTicks(adaptiveCooldownTicks)
    .cooldownTime(adaptiveCooldownTime)
    .onEngineStop(() => {
      emit("simulation-state", false);
      fadeInHalos();
      // 2026-05-12 回退:onEngineStop 只在沙盘模式调 forceUpdateLinkOpacity
      //   原 D.7 改无条件调用以"防 force-graph reload 不调 accessor",但实测
      //   反而把已经设好的对色 color 在 stop 时覆盖错(用户报告"图1进入正常 →
      //   图2变色")。让 force-graph 自管 line color,我们只在沙盘玻璃态切换时
      //   强制刷一次 opacity(沙盘是非默认态,需要主动同步)。
      if (props.sandboxMode) {
        forceUpdateLinkOpacity();
        // Sprint D.7:reload graphData 后新 group 不自带 cfHalo(已改为 lazy 创建),
        //   watch counterfactualSourceIds 不会 fire(props 没变),source 节点的
        //   光晕环就丢了。此处兜底 ensure 一次:engine stop 时所有新 group 已装载,
        //   nodeCoreMeshes 已 populated,ensureCfHalo 能拿到 parent group。
        applyCounterfactualHalos();
      }
    })
    .onNodeClick(((n: unknown, ev: unknown) => {
      const node = n as GraphNode;
      const me = ev as MouseEvent | undefined;
      // 修饰键路由:
      //   Shift+click  → 关系连接(1.M.1.E)
      //   Ctrl/Cmd+click → 框选切换(1.M.3,跨平台:Windows ctrl,Mac cmd)
      //   普通 click   → 节点编辑 drawer
      if (me?.shiftKey) emit("node-shift-click", node);
      else if (me?.ctrlKey || me?.metaKey) emit("node-frame-click", node);
      else emit("node-click", node);
    }) as never)
    // 2026-05 P2 + P3:hover 节点 → 钉住该节点防震动 + emit 给父组件浮窗
    // 离开节点(n=null) → 解 pin + emit null
    .onNodeHover(((n: unknown) => {
      const node = n as GraphNode | null;
      // 1. 解上一个 pin
      if (pinnedHoverNode && pinnedHoverNode !== node) {
        pinnedHoverNode.fx = null;
        pinnedHoverNode.fy = null;
        pinnedHoverNode.fz = null;
        pinnedHoverNode = null;
      }
      // 2. 钉住当前 hover 节点 — fx/fy/fz 设为当前位置后,d3-force 不再移动它
      //    用户瞄准时节点不再小幅震动,第一次 click 命中率从 ~70% → ~98%
      if (node) {
        const n2 = node as typeof pinnedHoverNode;
        if (n2) {
          n2.fx = n2.x ?? 0;
          n2.fy = n2.y ?? 0;
          n2.fz = n2.z ?? 0;
          pinnedHoverNode = n2;
        }
      }
      emit(
        "node-hover",
        node,
        node ? { x: lastMouseX, y: lastMouseY } : null,
      );
    }) as never)
    .onLinkClick(((l: unknown) => emit("link-click", l as GraphLink)) as never)
    // Sprint 1.M.1.C:拖动结束 → emit 新位置(force-graph 自带 enableNodeDrag,默认开)
    // 父组件 ProjectGraphView debounce 后 PATCH /characters/{id} 持久化
    .onNodeDragEnd(((n: unknown) => {
      const node = n as GraphNode & { x?: number; y?: number; z?: number };
      emit("node-dragend", node, {
        x: node.x ?? 0,
        y: node.y ?? 0,
        z: node.z ?? 0,
      });
    }) as never)
    // Sprint 1.M.1.D:右键画布空白 → 父组件弹 NodeCreatorMenu
    // event 是 MouseEvent,clientX/Y 给菜单定位
    .onBackgroundRightClick(((ev: unknown) => {
      const me = ev as MouseEvent;
      me.preventDefault();   // 屏蔽浏览器默认右键菜单
      emit("background-rclick", { x: me.clientX, y: me.clientY });
    }) as never);

  // 切到动态 linkOpacity(每帧根据 dimMode 计算)
  graph.linkOpacity(((l: unknown) => dynamicLinkOpacity(l as GraphLink)) as never);

  graph.cameraPosition({ x: 0, y: 0, z: 220 });

  fpsRaf = requestAnimationFrame(tickFps);

  const onResize = () => {
    if (!containerRef.value || !graph) return;
    graph
      .width(containerRef.value.clientWidth)
      .height(containerRef.value.clientHeight);
  };
  window.addEventListener("resize", onResize);
  onResize();
  // 2026-06-02 hotfix:记录 listener 以便 onBeforeUnmount 移除(治内存泄漏)
  resizeListenerCleanup = () => window.removeEventListener("resize", onResize);
}

/**
 * 数据 watcher:**只看 reference 变化,绝不能用 deep:true**!
 * 见迁移指南"前端踩坑"小节。
 *
 * 2026-05 P4:增量更新优化 —
 *   字段 update(节点 id 集合不变,只是 name/identity 等字段变)→ 不重启 simulation
 *   节点增删(id 集合变)→ 走老路径 graph.graphData(d) 重启 simulation
 *
 * 优化前:每次 ProjectGraphView 编辑保存 → useProjectGraph.load() reload 整个
 *         graphData → 此 watch 触发 → graph.graphData(d) 重启物理引擎 →
 *         416 节点重新 layout 1-2s,用户看到节点抖动
 * 优化后:字段 update 走 mutate-in-place 路径 → 不重启,节点位置不动
 */
watch(
  () => props.data,
  (d, oldD) => {
    if (!graph) return;

    // 第一次 watch 触发(oldD undefined),走原路径
    if (!oldD) {
      nodeCoreMeshes.clear();
      nodeHaloMeshes.clear();
      disposeAllCfHalos();   // D.7:dispose 替代 clear,释放 GPU 顶点/材质
      nodeHaloOriginalColor.clear();
      emit("simulation-state", true);
      graph.graphData(d as never);
      return;
    }

    const oldIds = new Set(oldD.nodes.map((n) => n.id));
    const newIds = new Set(d.nodes.map((n) => n.id));
    const sameStructure =
      d.nodes.length === oldD.nodes.length
      && d.links.length === oldD.links.length
      && oldD.nodes.every((n) => newIds.has(n.id))
      && d.nodes.every((n) => oldIds.has(n.id));

    if (sameStructure) {
      // 字段 update(节点 id 集合不变):只 mutate force-graph 内部 nodes 字段,
      // 保留 x/y/z/vx/vy/vz/fx/fy/fz 不动 → simulation 不重启,节点不抖
      // 2026-06-06:三方库 GraphData<NodeObject> 与我们的 GraphNode 类型不重叠,
      // 用 as unknown as 双 cast 让 TS 接受(运行时 force-graph 透传我们传入的 nodes 对象)
      const internalData = graph.graphData() as unknown as {
        nodes: Array<GraphNode & Record<string, unknown>>;
      };
      const idToInternal = new Map<string, GraphNode & Record<string, unknown>>();
      for (const n of internalData.nodes) {
        idToInternal.set((n as GraphNode).id, n);
      }
      // merge 关键展示字段(name 影响 sprite text;identity 影响 hover 浮窗;tone 影响色)
      const displayKeys = ["name", "identity", "tone", "type"] as const;
      let meshDirty = false;
      for (const newN of d.nodes) {
        const internal = idToInternal.get(newN.id);
        if (!internal) continue;
        for (const k of displayKeys) {
          const newRec = newN as unknown as Record<string, unknown>;
          if (internal[k] !== newRec[k]) {
            (internal as Record<string, unknown>)[k] = newRec[k];
            meshDirty = true;
          }
        }
      }
      if (meshDirty) {
        // 清 mesh 缓存让 force-graph 下次 refresh 调 nodeThreeObject 重做 sprite
        // (force-graph 不支持单节点 invalidate;清全部但节点位置由 internal 对象保留)
        nodeCoreMeshes.clear();
        nodeHaloMeshes.clear();
        disposeAllCfHalos();   // D.7:dispose 替代 clear,释放 GPU
        nodeHaloOriginalColor.clear();
        graph.refresh();
        // Sprint D.7:字段 update 路径走 graph.refresh(),**不触发 onEngineStop**,
        //   所以 cfHalo 不会被那里的兜底 ensure 回来。RAF 后主动 ensure(force-graph
        //   下一帧 render 时调 makeNodeObject 重建 group,然后能拿到 parent.group)。
        if (props.sandboxMode) {
          requestAnimationFrame(() => applyCounterfactualHalos());
        }
      }
      return;
    }

    // 节点增删 → 老路径(重启 simulation 是必要的,节点结构变了)
    nodeCoreMeshes.clear();
    nodeHaloMeshes.clear();
    disposeAllCfHalos();   // D.7:dispose 替代 clear,释放 GPU
    nodeHaloOriginalColor.clear();
    emit("simulation-state", true);
    graph.graphData(d as never);
    // Sprint D.7 修复:原 D.2.B 用 queueMicrotask + setTimeout + RAF 三 pass 兜底,
    //   是早期没理解 force-graph 渲染时序时的过度防御。实际 RAF 单 pass 已能命中
    //   force-graph 内部 render 之前的时机(onEngineStop 仍兜底一次,见 init)。
    //   三 pass 在 600 关系场景做 1800 次冗余 opacity 写入,首次进入图谱 + reload
    //   都更慢;改单 pass 后省 ~67% 写入,reload 卡顿明显改善。
    if (props.sandboxMode) {
      requestAnimationFrame(forceUpdateLinkOpacity);
    }
  },
);

watch(
  () => props.zoomSensitivity,
  (s) => {
    if (!graph) return;
    const controls = graph.controls() as { zoomSpeed?: number } | undefined;
    if (controls) controls.zoomSpeed = s ?? 1.0;
  },
);

// dim 模式 + participants 变化 → 重新应用材质 / 缩放(纯视觉,不动相机)
watch(
  () => [props.dimMode, props.participants?.join("|")],
  () => {
    applyDimMode(props.dimMode ?? false);
  },
);

/**
 * 单独监听 dimMode 真实切换,做相机 dolly。
 *
 * 必须用 prev 比对避免假触发——participants 变化也会让上面那个 watch 跑,
 * 但相机动画一旦多跑一次,OrbitControls 就被覆盖,图谱拖不动。
 */
watch(
  () => props.dimMode,
  (dim, prevDim) => {
    if (dim === prevDim) return;
    if (!graph) return;
    dollyCameraTo(dim ? 180 : 220, 1200);
  },
);

// 1.M.2:refinedNodeIds 变化驱动 refined pulse 启停
watch(
  () => props.refinedNodeIds?.join("|") ?? "",
  (joined) => {
    stopRefinedPulse();   // 先收尾上一轮(还原 baseline)
    if (joined.length > 0) startRefinedPulse();
  },
);

// 1.M.3:trailNodeIds 变化驱动稳态光迹(无 RAF)
watch(
  () => props.trailNodeIds?.join("|") ?? "",
  () => {
    applyTrailGlow(props.trailNodeIds ?? []);
  },
);

// Sprint 2.C:affectedNodeIds 变化驱动"影响范围"baseline 高亮
const lastAffectedSet = new Set<string>();
function applyAffectedHighlight(ids: string[]) {
  const newSet = new Set(ids);
  // 进入 affected:halo 强化 + emissive 加亮
  for (const id of newSet) {
    if (lastAffectedSet.has(id)) continue;
    const halo = nodeHaloMeshes.get(id);
    if (halo) {
      const m = halo.material as THREE.MeshBasicMaterial;
      m.opacity = 0.35;
      halo.scale.setScalar(1.15);
    }
    const core = nodeCoreMeshes.get(id);
    if (core) {
      const mat = core.material as THREE.MeshStandardMaterial;
      mat.emissiveIntensity = 0.85;
    }
  }
  // 退出 affected:还原到 baseline
  for (const id of lastAffectedSet) {
    if (newSet.has(id)) continue;
    const halo = nodeHaloMeshes.get(id);
    if (halo) {
      const m = halo.material as THREE.MeshBasicMaterial;
      m.opacity = 0.12;
      halo.scale.setScalar(1.0);
    }
    const core = nodeCoreMeshes.get(id);
    if (core) {
      const mat = core.material as THREE.MeshStandardMaterial;
      mat.emissiveIntensity = 0.5;
    }
  }
  lastAffectedSet.clear();
  for (const id of newSet) lastAffectedSet.add(id);
}

watch(
  () => props.affectedNodeIds?.join("|") ?? "",
  () => {
    applyAffectedHighlight(props.affectedNodeIds ?? []);
  },
);

// dimMode 切换时也要重新评估 trail(dim 时不叠,dim 退去后才能露 trail)
watch(
  () => props.dimMode,
  () => {
    applyTrailGlow(props.trailNodeIds ?? []);
  },
);

// ============================================================
// Sprint D.2.A — 沙盘玻璃态 + 反事实光晕环
// ============================================================

/** 沙盘模式玻璃态 — 切沙盘时强制所有节点 core mesh 到新 baseline。
 *
 *  注意:这是**强制覆盖**,会暂时压制 trail glow / refined / pulse 等
 *  机制留下的 emissiveIntensity 状态。下一次 pulse cycle 会按它自己的
 *  逻辑覆盖 emissive(玻璃态 + pulse 共存:节点半透明但仍闪)。
 *
 *  link 的 opacity 由 dynamicLinkOpacity 每帧动态算,沙盘开自动走 0.15
 *  玻璃态分支,无需此处处理。
 */
function applyGlassMode() {
  const enabled = props.sandboxMode === true;
  const targetOpacity = enabled ? NODE_OPACITY_SANDBOX : NODE_OPACITY_DEFAULT;
  const targetEmissive = enabled ? NODE_EMISSIVE_SANDBOX : NODE_EMISSIVE_DEFAULT;
  for (const core of nodeCoreMeshes.values()) {
    const mat = core.material as THREE.MeshStandardMaterial;
    mat.opacity = targetOpacity;
    mat.emissiveIntensity = targetEmissive;
  }
  // halo 也淡化:玻璃态下原 halo 跟着褪去,让 cfHalo 真正独占视觉
  for (const halo of nodeHaloMeshes.values()) {
    const m = halo.material as THREE.MeshBasicMaterial;
    m.opacity = enabled ? 0.04 : 0.12;
  }
}

watch(
  () => props.sandboxMode,
  () => {
    applyGlassMode();
    // 3d-force-graph 把 link opacity 算一次后缓存到 Three.js LineBasicMaterial,
    // props.sandboxMode 变化时它**不**主动重新评估。手动迭代所有 link material
    // 强制更新(比 graph.refresh() 轻 — refresh 会重建所有节点 mesh)
    forceUpdateLinkOpacity();
  },
);

watch(
  () => props.graphStrengthThreshold,
  () => {
    // D.2.B fix:阈值变化 → link 数组**不变**(strength 过滤移到 runtime opacity),
    // force-graph 不重建 line,只强制重设所有 link material.opacity → 无闪烁
    forceUpdateLinkOpacity();
  },
);

/** 手动迭代所有 link material 强制重算 opacity。比 graph.refresh() 轻量
 *  10 倍,因为不重建任何 mesh。force-graph 把每条 link 存在 __lineObj
 *  内部字段(LineBasicMaterial),我们直接遍历改 opacity。
 *
 *  **不动 color** — 2026-05-12 用户报告"进入图谱后 color 突变成单色"
 *  之前为防 force-graph accessor 不被调试图主动 set color,反而覆盖了 force-graph
 *  设的对色色值。回退:让 force-graph 完全自管 color(linkColor accessor 在
 *  graphData / refresh 时自然会被调用),此函数只改 opacity。
 */
function forceUpdateLinkOpacity() {
  if (!graph) return;
  // 2026-06-06:同上,三方库类型与我们的 GraphLink 不重叠,as unknown as 双 cast
  const internalData = graph.graphData() as unknown as {
    links: Array<{ __lineObj?: THREE.Line; __arrowObj?: THREE.Mesh } & GraphLink>;
  };
  for (const link of internalData.links) {
    const newOpacity = dynamicLinkOpacity(link);
    const line = link.__lineObj;
    if (line && line.material) {
      const mat = line.material as THREE.LineBasicMaterial;
      mat.opacity = newOpacity;
      mat.transparent = newOpacity < 1;
      mat.needsUpdate = true;
    }
    // 兼容 force-graph 内部可能用的 arrow / particle 子对象
    const arrow = link.__arrowObj;
    if (arrow && (arrow as THREE.Mesh).material) {
      const am = (arrow as THREE.Mesh).material as THREE.MeshBasicMaterial;
      am.opacity = newOpacity;
      am.transparent = newOpacity < 1;
    }
  }
}

/** Sprint D.7 修复:cfHalo Torus 按需 lazy 创建。
 *  原 D.2.A 实现每节点都建 Torus(无论是否为反事实源 + 无论是否沙盘开),
 *  导致 400 节点 × ~320 顶点 = 128K 顶点 GPU 数据被占用,正常视图也吃帧。
 *  改成"沙盘开 + 节点是 source"才创建,沙盘关时全 dispose 释放 GPU。
 */
function ensureCfHalo(nodeId: string, color: number): void {
  const existing = nodeCfHaloMeshes.get(nodeId);
  if (existing) {
    // 已存在 — 只更新颜色 / opacity(切换源类型时)
    const mat = existing.material as THREE.MeshBasicMaterial;
    mat.color.setHex(color);
    mat.opacity = CF_HALO_OPACITY_ACTIVE;
    return;
  }
  const core = nodeCoreMeshes.get(nodeId);
  if (!core) return;
  const group = core.parent as THREE.Group | null;
  if (!group) return;
  const radius = group.userData.cfHaloRadius as number | undefined;
  if (radius === undefined) return;
  // Sprint D.7:分段从 10×32 降到 6×16(顶点 -70%),3D 视角下视觉无差
  const geo = new THREE.TorusGeometry(radius, 0.28, 6, 16);
  const mat = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: CF_HALO_OPACITY_ACTIVE,
    depthWrite: false,
  });
  const cfHalo = new THREE.Mesh(geo, mat);
  // Torus 默认沿 XY 平面,略微倾斜让用户看见环面(纯水平太薄)
  cfHalo.rotation.x = Math.PI / 2.6;
  group.add(cfHalo);
  nodeCfHaloMeshes.set(nodeId, cfHalo);
}

/** 单个节点 dispose — 从 group 移除 + 释放 geometry/material + 从 Map 删 */
function disposeCfHalo(nodeId: string): void {
  const existing = nodeCfHaloMeshes.get(nodeId);
  if (!existing) return;
  const group = existing.parent as THREE.Group | null;
  if (group) group.remove(existing);
  existing.geometry.dispose();
  (existing.material as THREE.Material).dispose();
  nodeCfHaloMeshes.delete(nodeId);
}

/** 全部 dispose — 沙盘关 / graphData 重建 / 卸载时调用。
 *  顺带修了 D.2.A 时代埋的内存泄漏:原 4 处 nodeCfHaloMeshes.clear() 都没 dispose,
 *  GPU geometry/material 内存慢性堆积;改用此函数显式释放。
 */
function disposeAllCfHalos(): void {
  for (const cfHalo of nodeCfHaloMeshes.values()) {
    const group = cfHalo.parent as THREE.Group | null;
    if (group) group.remove(cfHalo);
    cfHalo.geometry.dispose();
    (cfHalo.material as THREE.Material).dispose();
  }
  nodeCfHaloMeshes.clear();
}

/** 沙盘模式 + 反事实源节点 → 光晕环显;否则隐藏 / dispose。
 *
 *  视觉:
 *    - character 节点 → 紫罗兰环
 *    - event 节点     → 蓝环
 *  无动画(纯 opacity / color 切换),由 watch 在 prop 变化时即时触发。
 *
 *  与其它 pulse / trail 机制独立:cfHalo 是按需挂在节点 group 上的额外 mesh,
 *  applyAffectedHighlight / startPulse / startTrail 完全不碰它,无冲突。
 */
function applyCounterfactualHalos() {
  if (props.sandboxMode !== true) {
    // 沙盘关 → 全部 dispose,释放 GPU
    disposeAllCfHalos();
    return;
  }
  const charSet = new Set(props.counterfactualSourceIds?.character ?? []);
  const eventSet = new Set(props.counterfactualSourceIds?.event ?? []);
  // 1. ensure source 节点的 cfHalo(character 优先,与 BFS 仲裁口径一致)
  for (const id of charSet) ensureCfHalo(id, CF_HALO_COLOR_CHARACTER);
  for (const id of eventSet) {
    if (!charSet.has(id)) ensureCfHalo(id, CF_HALO_COLOR_EVENT);
  }
  // 2. dispose 已不是 source 的 cfHalo(用户撤反事实变量场景)
  for (const id of [...nodeCfHaloMeshes.keys()]) {
    if (!charSet.has(id) && !eventSet.has(id)) disposeCfHalo(id);
  }
}

watch(
  // 监听 3 个依赖:sandboxMode 切换 / character source 变化 / event source 变化
  () => [
    props.sandboxMode,
    props.counterfactualSourceIds?.character.join("|") ?? "",
    props.counterfactualSourceIds?.event.join("|") ?? "",
  ],
  () => {
    applyCounterfactualHalos();
    // Sprint D.2.B:沙盘开 / 源节点变 → 触发 BFS 影响波纹一次
    // 沙盘关 → 停 RAF + 还原 halo
    if (props.sandboxMode) {
      triggerRippleAnimation();
    } else {
      stopRippleAnimation();
    }
  },
);

// ============================================================
// Sprint D.2.B — BFS 影响波纹动画
// ============================================================

/** 节点 halo 的原始颜色(makeNodeObject 时记下)— 波纹临时染色后还原用 */
const nodeHaloOriginalColor = new Map<string, number>();

/** 波纹 RAF + 状态 */
let rippleRaf: number | null = null;
/** nodeId → ripple 开始的全局时间戳(performance.now())*/
const rippleStartTimes = new Map<string, number>();
/** nodeId → 该节点波纹用什么颜色(取最近源节点的类型色)*/
const rippleColorByNode = new Map<string, number>();

/** 构建邻接表(无向)— BFS 用 */
function buildAdjacency(): Map<string, string[]> {
  const adj = new Map<string, string[]>();
  for (const link of props.data.links) {
    const s =
      typeof link.source === "string"
        ? link.source
        : (link.source as GraphNode).id;
    const t =
      typeof link.target === "string"
        ? link.target
        : (link.target as GraphNode).id;
    if (!adj.has(s)) adj.set(s, []);
    if (!adj.has(t)) adj.set(t, []);
    adj.get(s)!.push(t);
    adj.get(t)!.push(s);
  }
  return adj;
}

/** 多源 BFS:返回每个节点的 (depth, sourceType)。
 *  depth=0 = 源节点本身;maxHops 之外不入。
 *  sourceType = 最早触达此节点的源节点类型(character > event 仲裁,因 character 先入队)。
 */
function bfsFromSources(
  characterSources: string[],
  eventSources: string[],
  maxHops: number,
): Map<string, { depth: number; color: number }> {
  type QueueItem = { id: string; depth: number; color: number };
  const result = new Map<string, { depth: number; color: number }>();
  const queue: QueueItem[] = [];

  // character 优先入队(若同时被两源触达,显紫色)
  for (const id of characterSources) {
    if (!result.has(id)) {
      result.set(id, { depth: 0, color: CF_HALO_COLOR_CHARACTER });
      queue.push({ id, depth: 0, color: CF_HALO_COLOR_CHARACTER });
    }
  }
  for (const id of eventSources) {
    if (!result.has(id)) {
      result.set(id, { depth: 0, color: CF_HALO_COLOR_EVENT });
      queue.push({ id, depth: 0, color: CF_HALO_COLOR_EVENT });
    }
  }

  if (queue.length === 0) return result;

  const adj = buildAdjacency();

  // 标准 BFS
  while (queue.length > 0) {
    const cur = queue.shift()!;
    if (cur.depth >= maxHops) continue;
    const neighbors = adj.get(cur.id) ?? [];
    for (const n of neighbors) {
      if (result.has(n)) continue;
      result.set(n, { depth: cur.depth + 1, color: cur.color });
      queue.push({ id: n, depth: cur.depth + 1, color: cur.color });
    }
  }
  return result;
}

/** 触发一次波纹动画:从源节点 BFS 扩散,按 depth 错层激活 */
function triggerRippleAnimation() {
  const charSources = props.counterfactualSourceIds?.character ?? [];
  const eventSources = props.counterfactualSourceIds?.event ?? [];
  if (charSources.length === 0 && eventSources.length === 0) {
    return;   // 无源 → 无波纹
  }

  const bfs = bfsFromSources(charSources, eventSources, RIPPLE_MAX_HOPS);
  if (bfs.size === 0) return;

  // 录入 ripple 起始时间 — depth 越深延迟越长(涟漪感)
  const now = performance.now();
  rippleStartTimes.clear();
  rippleColorByNode.clear();
  for (const [id, info] of bfs) {
    rippleStartTimes.set(id, now + info.depth * RIPPLE_PER_DEPTH_DELAY_MS);
    rippleColorByNode.set(id, info.color);
  }

  // 启动 RAF(若已在跑则继续,不重启)
  if (rippleRaf === null) {
    rippleRaf = requestAnimationFrame(rippleTick);
  }
}

/** RAF 帧:对每个波纹节点按 elapsed 时间算 opacity / scale / color,过期则停 */
function rippleTick() {
  const now = performance.now();
  let stillActive = false;

  for (const [id, startAt] of rippleStartTimes) {
    const elapsed = now - startAt;
    const halo = nodeHaloMeshes.get(id);
    if (!halo) continue;
    const mat = halo.material as THREE.MeshBasicMaterial;

    if (elapsed < 0) {
      // 该节点还没到激活时刻(depth 大的节点延迟入)
      stillActive = true;
      continue;
    }
    if (elapsed > RIPPLE_DURATION_MS) {
      // 该节点波纹已过:还原 baseline + 还原颜色
      mat.opacity = props.sandboxMode ? 0.04 : 0.12;
      halo.scale.setScalar(1.0);
      const origColor = nodeHaloOriginalColor.get(id);
      if (origColor !== undefined) mat.color.setHex(origColor);
      continue;
    }

    // 进行中:ease-in-out(sin 半圆)
    const t = elapsed / RIPPLE_DURATION_MS;       // 0..1
    const wave = Math.sin(t * Math.PI);           // 0→1→0 单峰
    const sandboxBaselineOp = props.sandboxMode ? 0.04 : 0.12;
    mat.opacity = sandboxBaselineOp + (RIPPLE_PEAK_HALO_OPACITY - sandboxBaselineOp) * wave;
    halo.scale.setScalar(1.0 + (RIPPLE_PEAK_HALO_SCALE - 1.0) * wave);
    const targetColor = rippleColorByNode.get(id);
    if (targetColor !== undefined) {
      mat.color.setHex(targetColor);
    }
    stillActive = true;
  }

  if (stillActive) {
    rippleRaf = requestAnimationFrame(rippleTick);
  } else {
    rippleRaf = null;
    rippleStartTimes.clear();
    rippleColorByNode.clear();
  }
}

/** 沙盘关闭 / 组件卸载 → 停 RAF + 把所有 halo 还原到合理 baseline */
function stopRippleAnimation() {
  if (rippleRaf !== null) {
    cancelAnimationFrame(rippleRaf);
    rippleRaf = null;
  }
  rippleStartTimes.clear();
  rippleColorByNode.clear();
  // 还原所有可能被波纹改过的 halo:opacity / scale / color
  for (const [id, halo] of nodeHaloMeshes) {
    const mat = halo.material as THREE.MeshBasicMaterial;
    mat.opacity = props.sandboxMode ? 0.04 : 0.12;
    halo.scale.setScalar(1.0);
    const origColor = nodeHaloOriginalColor.get(id);
    if (origColor !== undefined) mat.color.setHex(origColor);
  }
}

onMounted(init);
onBeforeUnmount(() => {
  if (fpsRaf !== null) cancelAnimationFrame(fpsRaf);
  if (pulseRaf !== null) cancelAnimationFrame(pulseRaf);
  if (refinedPulseRaf !== null) cancelAnimationFrame(refinedPulseRaf);
  if (rippleRaf !== null) cancelAnimationFrame(rippleRaf);   // D.2.B
  // 2026-06-02 hotfix:清 resize listener(治内存泄漏)
  resizeListenerCleanup?.();
  resizeListenerCleanup = null;
  refinedBaseline.clear();
  trailBaseline.clear();   // 1.M.3
  rippleStartTimes.clear();   // D.2.B
  rippleColorByNode.clear();
  // Sprint D.7 修复:dispose cfHalo 必须在 graph._destructor() **之前**做,
  //   确保 mesh 还能从 group 移除 + 释放 GPU geometry/material
  disposeAllCfHalos();
  // 2026-05 P2/P3:解 hover pin + 撤 mousemove 监听
  if (pinnedHoverNode) {
    pinnedHoverNode.fx = null;
    pinnedHoverNode.fy = null;
    pinnedHoverNode.fz = null;
    pinnedHoverNode = null;
  }
  containerRef.value?.removeEventListener("mousemove", onContainerMouseMove);
  if (graph) {
    graph._destructor?.();
    graph = null;
  }
  nodeCoreMeshes.clear();
  nodeHaloMeshes.clear();
  nodeHaloOriginalColor.clear();
});
</script>

<template>
  <!-- D.5 a11y:3D 图谱 canvas 给屏幕阅读器看的语义描述。
       图谱本身是图形,无法让 screen reader "看图",但 aria-label + role 让用户
       知道当前是一个交互式 3D 图谱;关键统计走顶栏 "N 节点 · M 关系"那块文字
       (那块也是 screen reader 能读到的)。 -->
  <div
    ref="containerRef"
    class="crystal-graph"
    role="img"
    aria-label="3D 关系图谱:可视化项目角色与关系的力导向布局,需要鼠标 / 触控操作。键盘用户可通过顶栏「反事实」「沙盘」「阈值」按钮等价访问主要功能。"
  />
</template>

<style scoped>
.crystal-graph {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
}
</style>
