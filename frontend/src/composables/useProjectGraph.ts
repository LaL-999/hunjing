/**
 * useProjectGraph — 从后端 /api/projects/:id/graph 取数据,
 * transform 成 CrystalGraph 期望的 ForceGraphData 结构。
 *
 * 替代旧的 useGraphData(后者从 /data/graphs/ch74.json 静态加载,已删除)。
 *
 * 数据映射:
 *   character → GraphNode (type=PERSON, tone 从 personality 推断)
 *   event     → GraphNode (type=EVENT)
 *   relationship → GraphLink (type=relationship.type)
 *   event.participants → GraphLink (event → character, type="参与")
 *
 * Sprint 2.E polish:接受 `() => string` getter 而非 string,这样侧栏切换项目
 * (Vue Router 复用 view 实例,只换 props.id)时,内部 watch 自动重新拉数据。
 * 不再让调用方自行 watch + 手动 reload。
 */
import { ref, watch, type Ref } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  RELATIONSHIP_STRENGTH_SCORE,
  type Character,
  type ProjectEvent,
  type Project,
  type Relationship,
} from "../api/types";
import type { CharacterTone, ForceGraphData, GraphLink, GraphNode } from "../types/graph";

interface ProjectGraphResponse {
  project: Project;
  characters: Character[];
  relationships: Relationship[];
  events: ProjectEvent[];
}

/** 简单关键词匹配推断性格基调,与旧 useGraphData 对齐。 */
function inferTone(personality: string): CharacterTone | undefined {
  if (!personality) return undefined;
  const p = personality;
  if (/[泼辣强势刚烈热烈精明权力急躁]/.test(p)) return "hot";
  if (/[多愁敏感孤高冷峭清傲内向害羞]/.test(p)) return "cold";
  if (/[痴情怜爱多情温和细腻]/.test(p)) return "tender";
  if (/[阴沉刻薄狠毒算计仗势阴鸷]/.test(p)) return "shadow";
  if (/[端方稳重持重藏愚守拙沉稳]/.test(p)) return "calm";
  if (/[忠义朴拙真诚委屈认真好学]/.test(p)) return "honest";
  return "calm";
}

/** event.description 截短为节点名(前 16 字 + ...) */
function eventNodeName(desc: string): string {
  if (desc.length <= 16) return desc;
  return desc.slice(0, 16) + "...";
}

export function transformBackendGraph(
  resp: ProjectGraphResponse,
): ForceGraphData {
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];

  // 1. 角色节点
  // Sprint 1.M.1.C:把后端持久化的 position 传给 force-graph 作初始位置;
  // 三轴全 0 视为"还没拖过"— force-graph 会自己跑力导向放节点(原行为)
  for (const c of resp.characters) {
    const hasPos =
      (c.position_x ?? 0) !== 0 ||
      (c.position_y ?? 0) !== 0 ||
      (c.position_z ?? 0) !== 0;
    nodes.push({
      id: c.id,
      name: c.name,
      type: "PERSON",
      aliases: [],
      description: c.identity || undefined,
      tone: inferTone(c.personality),
      ...(hasPos
        ? { x: c.position_x, y: c.position_y, z: c.position_z }
        : {}),
    });
  }

  // 2. 事件节点
  for (const ev of resp.events) {
    nodes.push({
      id: ev.id,
      name: eventNodeName(ev.description),
      type: "EVENT",
      aliases: [],
      description: ev.description,
    });
  }

  // 已存 node id set,用于过滤掉 invalid link(指向已删除节点的 link)
  // 3d-force-graph 拿到 invalid link 会导致整个仿真崩(只剩一个 node)
  const validNodeIds = new Set(nodes.map((n) => n.id));

  // 3. 关系连线 — 跳过 source/target 任一不存在的(防御层;后端 DB 已有 ON DELETE CASCADE)
  // Sprint D.2.B fix:**不**在这里按 threshold 过滤 — 否则用户调阈值滑块时
  // links 数组变 → force-graph 重建 line → 沙盘下闪一帧非玻璃态(线条 material
  // 创建那一刻还没走 accessor)。改成 link 上带 strengthScore,CrystalGraph 内部
  // 按 prop graphStrengthThreshold runtime 决定 opacity,force-graph 不重建。
  for (const r of resp.relationships) {
    if (!validNodeIds.has(r.source_id) || !validNodeIds.has(r.target_id)) continue;
    const score = RELATIONSHIP_STRENGTH_SCORE[r.strength] ?? 50;
    links.push({
      source: r.source_id,
      target: r.target_id,
      type: r.type,
      description: r.description || undefined,
      strengthScore: score,
    });
  }

  // 4. 事件参与连线(EVENT → PERSON) — events.participants JSON 数组没 SQL 外键,
  //    可能含已删 character_id 残留,**必须过滤**,否则 3D 图谱整个崩
  for (const ev of resp.events) {
    for (const charId of ev.participants) {
      if (!validNodeIds.has(charId)) continue;   // 跳过指向已删角色的 link
      links.push({
        source: ev.id,
        target: charId,
        type: "参与",
      });
    }
  }

  return { nodes, links };
}

export interface UseProjectGraphReturn {
  loading: Ref<boolean>;
  error: Ref<string | null>;
  graphData: Ref<ForceGraphData | null>;
  project: Ref<Project | null>;
  load: () => Promise<void>;
}

export function useProjectGraph(
  getProjectId: () => string,
): UseProjectGraphReturn {
  const loading = ref(false);
  const error = ref<string | null>(null);
  const graphData = ref<ForceGraphData | null>(null);
  const project = ref<Project | null>(null);

  async function load() {
    const pid = getProjectId();
    if (!pid) {
      // id 为空(路由未就绪)— 安静返回,等下一次 watch 触发
      return;
    }
    loading.value = true;
    error.value = null;
    try {
      const resp = await api.get<ProjectGraphResponse>(
        `/projects/${pid}/graph`,
      );
      project.value = resp.project;
      graphData.value = transformBackendGraph(resp);
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : "加载失败";
    } finally {
      loading.value = false;
    }
  }

  // 侧栏切项目 → props.id 变 → 自动重新拉数据(用户期望"换项目就换数据")
  // immediate: true 替代调用方原本的 onMounted(load)
  watch(getProjectId, () => { void load(); }, { immediate: true });

  return { loading, error, graphData, project, load };
}
