"""Counterfactual Combination Service — Sprint 6.A2 CT(2026-05-21)。

用户在反事实工作台勾选 1-3 个反事实变量,每个 2 态(a=原 / b=改),
共 2^N 个组合 → 后端 fanout 启动 2^N 个 sim → 前端决策树视图展示所有产物。

组合语义:
  - tree_path 是长度 = 变量数的 ['a' | 'b'] 列表
  - 'a' 表示"该变量取原值"= 该反事实**不启用**(原作设定)
  - 'b' 表示"该变量取改值"= 该反事实**启用**

API:
  - enumerate_combinations(variables_count) → 笛卡尔积 2^N 个 tree_path
  - start_combination_run(...)               → 落 combination_run + 创建 2^N 个 sim
  - get_combination_tree(combo_run_id, user) → 拉树供前端渲染
  - preview_cost(variable_count, reshape%, target_chars) → 预估 token/时长/积分
"""
from __future__ import annotations

import itertools
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.counterfactual_combination_run import (
    CounterfactualCombinationRun,
    SelectedVariable,
)
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# ============================================================
# 异常
# ============================================================

class CombinationVariableInvalid(Exception):
    """选中的反事实变量不合法(不存在 / 不属于该项目 / 已 reverted)。"""


class CombinationLimitExceeded(Exception):
    """变量数 > 3 或其他限制。"""


class CombinationNotFound(Exception):
    """combination_run 不存在或不属于该用户。"""


# ============================================================
# enumerate
# ============================================================

def enumerate_combinations(variable_count: int) -> list[list[str]]:
    """给定变量数 N,返 2^N 个 tree_path 组合(每元素 'a' 或 'b')。

    示例:
      N=1 → [['a'], ['b']]
      N=2 → [['a','a'], ['a','b'], ['b','a'], ['b','b']]
      N=3 → 8 个

    顺序:按字典序(a 在前 b 在后),与 itertools.product 一致。
    """
    if variable_count < 1 or variable_count > 3:
        raise CombinationLimitExceeded(
            f"变量数必须 1-3,实际 {variable_count}"
        )
    return [list(p) for p in itertools.product("ab", repeat=variable_count)]


def tree_path_to_selected_cf_ids(
    tree_path: list[str],
    variables: list[SelectedVariable],
) -> list[str]:
    """把 tree_path 翻译成 selected_counterfactual_ids。

    'b' 表示该变量启用 → 收集对应 counterfactual_id
    'a' 表示该变量取原值 → 跳过

    全 'a' 路径 → 空列表(纯原作演)
    """
    if len(tree_path) != len(variables):
        raise ValueError(
            f"tree_path 长度 {len(tree_path)} != variables 数 {len(variables)}"
        )
    return [
        var.counterfactual_id
        for path_val, var in zip(tree_path, variables)
        if path_val == "b"
    ]


# ============================================================
# 校验
# ============================================================

def _validate_selected_variables(
    conn: sqlite3.Connection,
    project_id: str,
    variables: list[SelectedVariable],
) -> None:
    """校验所有反事实存在 + 属于该项目 + 未 reverted + counterfactual_id 不重复。"""
    if not variables:
        raise CombinationVariableInvalid("至少需要 1 个变量")
    if len(variables) > 3:
        raise CombinationLimitExceeded(
            f"最多 3 个变量(2^3=8 组合),实际 {len(variables)}"
        )
    seen: set[str] = set()
    for v in variables:
        if not v.counterfactual_id:
            raise CombinationVariableInvalid("counterfactual_id 不能为空")
        if v.counterfactual_id in seen:
            raise CombinationVariableInvalid(
                f"counterfactual_id {v.counterfactual_id} 重复"
            )
        seen.add(v.counterfactual_id)
        if not v.label_a or not v.label_b:
            raise CombinationVariableInvalid(
                f"counterfactual_id {v.counterfactual_id} 的 label_a/b 不能为空"
            )

    # 一次性查所有 ID 是否存在 + 属于该 project + 未 reverted
    ids = [v.counterfactual_id for v in variables]
    placeholders = ",".join("?" for _ in ids)
    rows = fetch_all(
        conn,
        f"""SELECT id, project_id, reverted_at
            FROM counterfactual_changes
            WHERE id IN ({placeholders})""",
        tuple(ids),
    )
    found_ids = {r["id"] for r in rows}
    missing = [i for i in ids if i not in found_ids]
    if missing:
        raise CombinationVariableInvalid(
            f"counterfactual_id 不存在: {missing}"
        )
    for r in rows:
        if r["project_id"] != project_id:
            raise CombinationVariableInvalid(
                f"counterfactual_id {r['id']} 不属于该项目"
            )
        if r["reverted_at"] is not None:
            raise CombinationVariableInvalid(
                f"counterfactual_id {r['id']} 已被 revert,不可用于组合"
            )


# ============================================================
# 创建批次 + fanout
# ============================================================

def start_combination_run(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    variables: list[SelectedVariable],
    *,
    divergence: str,
    reshape_percent: int,
    target_chars: int,
    style: str,
    custom_style_hint: Optional[str],
    use_outline_first: bool,
) -> tuple[str, list[str]]:
    """创建批次 + 落 N 个 sim(每个对应一个组合)。

    Returns:
      (combination_run_id, list_of_sim_ids)

    Raises:
      CombinationVariableInvalid / CombinationLimitExceeded
    """
    _validate_selected_variables(conn, project_id, variables)

    paths = enumerate_combinations(len(variables))
    total = len(paths)  # 2 / 4 / 8

    combo_run_id = uuid.uuid4().hex
    now = iso_now()

    # 落 combination_run 行(state=pending,启动后再 UPDATE 为 generating)
    execute(
        conn,
        """INSERT INTO counterfactual_combination_runs
            (id, project_id, user_id, selected_variables_json,
             total_combinations, state, error_message, created_at, completed_at)
           VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
        (
            combo_run_id, project_id, user_id,
            json.dumps(
                [v.to_dict() for v in variables],
                ensure_ascii=False,
            ),
            total, now,
        ),
    )
    conn.commit()

    # fanout 创建 N 个 sim
    # 用 create_simulation 复用现有 sim 创建链路(配额校验 / sim 落库 / 反事实 link)
    # 创建完后 UPDATE 把 combination_run_id + tree_path_json 写到 sim 行
    from app.services.simulation_service import create_simulation
    sim_ids: list[str] = []
    failed_paths: list[str] = []

    for path in paths:
        selected_cf_ids = tree_path_to_selected_cf_ids(path, variables)
        try:
            # mode='evolution' 因为组合树是"长篇 what-if 探索",非快速模式
            sim_id = create_simulation(
                conn,
                project_id=project_id,
                user_id=user_id,
                divergence=divergence,
                reshape_percent=reshape_percent,
                target_chars=target_chars,
                style=style,
                custom_style_hint=custom_style_hint,
                context_simulation_ids=None,
                selected_counterfactual_ids=selected_cf_ids,
                mode="evolution",
                use_outline_first=use_outline_first,
                anchor_event_id=None,
            )
            # 写回组合信息
            execute(
                conn,
                """UPDATE simulations
                   SET combination_run_id = ?, tree_path_json = ?
                   WHERE id = ?""",
                (
                    combo_run_id,
                    json.dumps(path, ensure_ascii=False),
                    sim_id,
                ),
            )
            conn.commit()
            sim_ids.append(sim_id)
        except Exception as e:  # noqa: BLE001
            # 单个 sim 创建失败不阻塞其他 — 记下失败 path,继续下个
            logger.warning(
                f"combination sim 创建失败 path={path}: {e}"
            )
            failed_paths.append("".join(path))

    # 整批 state 推导
    if not sim_ids:
        # 全部失败
        execute(
            conn,
            """UPDATE counterfactual_combination_runs
               SET state='failed', error_message=?, completed_at=?
               WHERE id=?""",
            (
                f"全部 {total} 个 sim 创建失败: {','.join(failed_paths)}",
                iso_now(),
                combo_run_id,
            ),
        )
        conn.commit()
        raise CombinationVariableInvalid(
            f"全部 {total} 个 sim 创建失败"
        )

    # 至少有一个 sim 成功 → state='generating'(等 sim 各自跑)
    err_msg = None
    if failed_paths:
        err_msg = f"部分组合失败: {','.join(failed_paths)}"
    execute(
        conn,
        """UPDATE counterfactual_combination_runs
           SET state='generating', error_message=?
           WHERE id=?""",
        (err_msg, combo_run_id),
    )
    conn.commit()

    return combo_run_id, sim_ids


# ============================================================
# 查询批次 + 树
# ============================================================

def get_combination_run(
    conn: sqlite3.Connection,
    combo_run_id: str,
    user_id: str,
) -> CounterfactualCombinationRun:
    """拉批次记录,严格校验属于该用户。

    Raises CombinationNotFound 若不存在 / 不属于该用户。
    """
    row = fetch_one(
        conn,
        "SELECT * FROM counterfactual_combination_runs WHERE id=?",
        (combo_run_id,),
    )
    if row is None or row["user_id"] != user_id:
        raise CombinationNotFound(
            f"combination_run {combo_run_id} 不存在或不属于该用户"
        )
    return CounterfactualCombinationRun.from_row(row)


def get_combination_leaves(
    conn: sqlite3.Connection,
    combo_run_id: str,
) -> list[dict]:
    """拉该批次下所有 sim 节点(叶节点),按 tree_path 字典序排序。

    每个 dict:
      {
        "simulation_id": str,
        "tree_path": ['a' | 'b', ...],
        "sim_state": str,
        "sim_narrative_chars": int,
        "sim_created_at": str,
        "sim_completed_at": Optional[str],
        # 2026-06-05 outline-first 模式 — sim 卡在 queued 等用户审核 outline 时
        "outline_id": Optional[str],
        "outline_state": Optional[str],   # drafting / awaiting_user / done / failed
      }
    """
    # LEFT JOIN simulation_outlines:outline-first 模式下,sim 创建后会先生成 outline
    # outline_state='awaiting_user' 时 sim_state 还停在 queued/pending,
    # UI 需要专门高亮"等审核"状态,引导用户去前往 outline 审核
    rows = fetch_all(
        conn,
        """SELECT s.id, s.state, s.narrative, s.tree_path_json,
                  s.created_at, s.completed_at,
                  so.id AS outline_id, so.state AS outline_state
           FROM simulations s
      LEFT JOIN simulation_outlines so ON so.simulation_id = s.id
           WHERE s.combination_run_id = ?
           ORDER BY s.tree_path_json ASC""",
        (combo_run_id,),
    )
    leaves: list[dict] = []
    for r in rows:
        try:
            tree_path = json.loads(r["tree_path_json"] or "[]")
            if not isinstance(tree_path, list):
                tree_path = []
        except (json.JSONDecodeError, TypeError):
            tree_path = []
        narr = r["narrative"] or ""
        leaves.append({
            "simulation_id": r["id"],
            "tree_path": [str(x) for x in tree_path],
            "sim_state": r["state"],
            "sim_narrative_chars": len(narr),
            "sim_created_at": r["created_at"],
            "sim_completed_at": r["completed_at"],
            "outline_id": r["outline_id"],
            "outline_state": r["outline_state"],
        })
    return leaves


def get_latest_combination_run_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
) -> Optional[str]:
    """返该项目最新一条 combination_run 的 id;若无,返 None。

    用于"组合树"入口智能跳转 — 用户已有运行 / 完成的批次时,
    直接跳进度页而不是配置页(避免覆盖现有进度)。
    """
    row = fetch_one(
        conn,
        """SELECT id FROM counterfactual_combination_runs
           WHERE project_id = ? AND user_id = ?
        ORDER BY created_at DESC
           LIMIT 1""",
        (project_id, user_id),
    )
    return row["id"] if row else None


def list_combination_runs_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    limit: int = 30,
) -> list[dict]:
    """返该项目所有 combination_runs 的简略列表 — 给"切批次"下拉用。

    每个 dict:{ id, state, total_combinations, created_at, done_count }
    按 created_at DESC 排序。
    """
    rows = fetch_all(
        conn,
        """SELECT
              cr.id, cr.state, cr.total_combinations,
              cr.created_at, cr.completed_at,
              (SELECT COUNT(*) FROM simulations s
                WHERE s.combination_run_id = cr.id
                  AND s.state IN ('completed','failed')) AS done_count
           FROM counterfactual_combination_runs cr
          WHERE cr.project_id = ? AND cr.user_id = ?
       ORDER BY cr.created_at DESC
          LIMIT ?""",
        (project_id, user_id, limit),
    )
    return [
        {
            "id": r["id"],
            "state": r["state"],
            "total_combinations": r["total_combinations"],
            "created_at": r["created_at"],
            "completed_at": r["completed_at"],
            "done_count": r["done_count"],
        }
        for r in rows
    ]


def delete_combination_run(
    conn: sqlite3.Connection,
    combo_run_id: str,
    user_id: str,
) -> int:
    """删除批次 + 级联删除所有下属 sim。返回删的 sim 数。

    2026-06-05:历史批次累积太多会让"切批次"下拉变成垃圾桶,加主动删除机制。
    级联策略:
      - 主表 counterfactual_combination_runs 删 1 行
      - simulations 表关联本批次的所有 sim 全删(SQL CASCADE 或显式 DELETE)
      - simulation_outlines / outline_scenes / simulation_scenes / scene_dialogues
        等子表通过外键 ON DELETE CASCADE 自动清

    Raises:
      CombinationNotFound:批次不存在或不属于该用户
    """
    # 1. 鉴权 — get_combination_run 内部校验 user_id
    get_combination_run(conn, combo_run_id, user_id)

    # 2. 查关联 sim 数(返回用)
    count_row = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM simulations WHERE combination_run_id = ?",
        (combo_run_id,),
    )
    sim_count = int(count_row["c"]) if count_row else 0

    # 3. 显式 DELETE simulations(外键 CASCADE 会清子表)
    #    主表 PK 引用在 simulations.combination_run_id,删主表前先删 sim 避免外键冲突
    execute(
        conn,
        "DELETE FROM simulations WHERE combination_run_id = ?",
        (combo_run_id,),
    )

    # 4. DELETE 批次本身
    execute(
        conn,
        "DELETE FROM counterfactual_combination_runs WHERE id = ?",
        (combo_run_id,),
    )
    conn.commit()
    return sim_count


def update_combination_run_state_after_sim_change(
    conn: sqlite3.Connection,
    combo_run_id: str,
) -> None:
    """sim 状态变化时(完成 / 失败)调一次,推算整批状态。

    规则:
      - 所有 sim 都 completed / failed → state='done'(完成,可能有失败,err_msg 保留)
      - 至少 1 个完成 + 还有未完成 → state='partial'
      - 全在 queued / generating → state='generating'(不变)
    """
    sim_rows = fetch_all(
        conn,
        "SELECT state FROM simulations WHERE combination_run_id=?",
        (combo_run_id,),
    )
    if not sim_rows:
        return
    terminal_states = {"completed", "failed"}
    completed_or_failed = sum(
        1 for r in sim_rows if r["state"] in terminal_states
    )
    total = len(sim_rows)

    new_state: Optional[str] = None
    completed_at: Optional[str] = None
    if completed_or_failed == total:
        new_state = "done"
        completed_at = iso_now()
    elif completed_or_failed > 0:
        new_state = "partial"

    if new_state is None:
        return
    execute(
        conn,
        """UPDATE counterfactual_combination_runs
           SET state=?, completed_at=COALESCE(?, completed_at)
           WHERE id=?""",
        (new_state, completed_at, combo_run_id),
    )
    conn.commit()


# ============================================================
# 成本预估
# ============================================================

@dataclass
class PreviewCost:
    total_combinations: int
    estimated_token_per_sim: int
    estimated_total_tokens: int
    estimated_minutes_per_sim: int
    estimated_total_minutes: int
    estimated_credits: int


def preview_cost(
    variable_count: int,
    reshape_percent: int,
    target_chars: int,
) -> PreviewCost:
    """给前端展示用户确认前 — 单个 sim ≈ rounds × 8K token / 1 min/scene。

    CT-OPT.1(2026-05-21)修正:
      - fanout 实际是**并发**(每 sim 起 daemon Thread,8 个同时跑)
      - estimated_total_minutes 不再是 N × 单 sim(那是串行估算),而是接近单 sim
      - 加 20% 的"并发开销 buffer"(API rate limit / 排队 / SSE 推送竞争)
      - estimated_total_tokens 仍 = N × 单 sim(总消耗不因并发减少)
      - estimated_credits 同上

    Token / 分钟 / 积分都是粗估,允许 30-50% 误差。
    """
    if variable_count < 1 or variable_count > 3:
        raise CombinationLimitExceeded(
            f"变量数必须 1-3,实际 {variable_count}"
        )
    total_combinations = 2 ** variable_count

    # 单 sim 估算
    # rounds 由 reshape% 派生(参考 simulation_service.reshape_to_rounds 公式)
    # 这里用粗估:rounds ≈ reshape_percent * 0.55(50% → 27 轮 / 90% → 50 轮)
    rounds_estimate = max(5, int(reshape_percent * 0.55))
    # 每轮 ≈ 8K token(director + agents×N + narrator + extractors)
    token_per_round = 8000
    estimated_token_per_sim = rounds_estimate * token_per_round
    # outline-first 多一次 outline + planner 每幕 ≈ 200 token
    estimated_token_per_sim += 5000  # outline overhead
    # 每幕 ≈ 1 分钟(单线程,evolution 模式 + multi-sample voting 3x)
    minutes_per_sim = rounds_estimate * 1
    # 积分 ≈ token / 100(简化估算,实际看 estimate_cost_yuan)
    credits_per_sim = max(1, estimated_token_per_sim // 100)

    # 总时长:并发 fanout → 总时长 ≈ 单 sim × (1 + 0.2 × 并发数量损耗)
    # 并发数大时 API rate limit / 排队会拉长每个 sim,但远小于"N × 串行"
    concurrency_overhead = 1.0 + 0.15 * (total_combinations - 1) / max(1, total_combinations)
    estimated_total_minutes = max(
        minutes_per_sim,
        int(round(minutes_per_sim * concurrency_overhead)),
    )

    return PreviewCost(
        total_combinations=total_combinations,
        estimated_token_per_sim=estimated_token_per_sim,
        estimated_total_tokens=estimated_token_per_sim * total_combinations,
        estimated_minutes_per_sim=minutes_per_sim,
        estimated_total_minutes=estimated_total_minutes,
        estimated_credits=credits_per_sim * total_combinations,
    )
