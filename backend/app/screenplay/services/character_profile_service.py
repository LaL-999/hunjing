"""角色档案聚合服务 — 阶段 8.2(2026-06-08)。

目标:把"已经算出来但没展示"的数据聚合成可浏览的角色页 + 关系图数据。

数据三路 JOIN:
  1. **身份 / 别名**:来自 sp_story_bibles 五张子表
     - sp_bible_characters: name, aka, description, is_protagonist
     - sp_bible_relationships: source/target/type/description
     - sp_bible_events: description, chapter, participants
  2. **戏份统计**:从最新 sp_screenplays.yaml_text 派生
     - 出场场数 / 出场章数 / 台词句数 / VO 句数 / 首次出场 scene
     - 戏份层级:主角(is_protagonist)→ 配角(≥5 句台词)→ 龙套(>0)→ 群演(0)
  3. **桥接资产**(可选 — 仅当 novel 已 link 父平台 project):
     - SP-2 surface_goal / deep_need / fatal_blind_spot / arc_from_to / secrets
     - SP-4 当前状态快照(position / emotion_vec / inventory)
     - SP-7 polarity 用于关系图边色

输出格式:适配前端关系图(d3-force / cytoscape / g6)+ 角色卡。

设计原则:
  - 纯计算,无 LLM
  - 桥接失败 / 未 link → 返普通版数据(无 bridge_assets 字段)
  - 跨用户隔离:必须传 user_id,内部校验 novel 归属
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import yaml as yamllib

from app.screenplay.db.connection import get_connection
from app.screenplay.services import ingest_service, screenplay_store

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约 — 角色卡 / 关系图
# ============================================================


@dataclass
class CharacterStats:
    """戏份统计 — 从 yaml 派生。"""

    scene_count: int = 0
    chapter_count: int = 0
    dialogue_count: int = 0
    voiceover_count: int = 0
    first_appearance_scene: Optional[str] = None  # scene_NNN
    first_appearance_number: Optional[int] = None
    role_tier: str = "extra"  # protagonist | supporting | bit_part | extra


@dataclass
class CharacterKeyEvent:
    """该角色参与的关键事件(来自 sp_bible_events)。"""

    description: str
    chapter_number: Optional[int] = None


@dataclass
class CharacterBridgeAssets:
    """桥接资产 — 仅当 novel 已 link 父平台 project 才填。

    所有字段都是可选的;只要有任一字段非空,就值得展示给前端。
    """

    # SP-2 角色驱动力
    surface_goal: Optional[str] = None
    deep_need: Optional[str] = None
    fatal_blind_spot: Optional[str] = None
    arc_from_to: Optional[str] = None
    secrets: list[dict] = field(default_factory=list)  # [{description, hidden_from?}]

    # SP-4 当前状态快照(若用户在父平台跑过推演)
    snapshot_position: Optional[str] = None
    snapshot_hp_status: Optional[str] = None
    snapshot_emotion_top: Optional[str] = None  # 主导情绪(top1 形如 "愤怒=0.8")
    snapshot_inventory: list[str] = field(default_factory=list)

    def has_any(self) -> bool:
        return bool(
            self.surface_goal or self.deep_need or self.fatal_blind_spot
            or self.arc_from_to or self.secrets
            or self.snapshot_position or self.snapshot_hp_status
            or self.snapshot_emotion_top or self.snapshot_inventory
        )


@dataclass
class CharacterProfile:
    """单角色完整档案。"""

    id: str
    name: str
    aka: list[str] = field(default_factory=list)
    description: str = ""
    is_protagonist: bool = False
    stats: CharacterStats = field(default_factory=CharacterStats)
    relationships: list[dict] = field(default_factory=list)
    # [{target_id, target_name, type, polarity?, description}]
    key_events: list[CharacterKeyEvent] = field(default_factory=list)
    bridge_assets: Optional[CharacterBridgeAssets] = None


@dataclass
class GraphNode:
    """关系图节点。"""

    id: str
    name: str
    role_tier: str
    weight: int  # 戏份(用于节点大小)
    is_protagonist: bool = False
    has_bridge_assets: bool = False  # 节点边框是否高亮"已接通父平台"


@dataclass
class GraphEdge:
    """关系图边。"""

    source: str
    target: str
    type: str            # 关系类型(夫妻 / 父子 / 同事...)
    description: str = ""
    polarity: Optional[str] = None  # positive | negative | neutral | None(来自 SP-7)


@dataclass
class CharacterProfilesResult:
    """endpoint 响应体。"""

    novel_id: str
    screenplay_id: str
    characters: list[CharacterProfile]
    graph: dict  # { nodes: [GraphNode], edges: [GraphEdge] }
    linked_project_id: Optional[str] = None  # 让前端知道桥接是否就位


# ============================================================
# 主入口
# ============================================================


def get_character_profiles(
    novel_id: str, user_id: str,
) -> CharacterProfilesResult | None:
    """组装角色档案。

    Returns:
        CharacterProfilesResult,或 None 当 novel/screenplay 不存在/不属于该用户

    流程:
      1. 校验 novel 归属 + 拿最新 screenplay
      2. 拉 bible 五件套
      3. 解析 yaml → 派生戏份统计
      4. 桥接资产(若已 link)
      5. 组装 graph 节点 + 边
    """
    # 1. 拉 novel + screenplay
    novel = ingest_service.get_novel(novel_id, user_id=user_id)
    if novel is None:
        return None
    screenplay_record = screenplay_store.get_latest_screenplay(
        novel_id, user_id=user_id,
    )
    if screenplay_record is None:
        # 没生成剧本 → 用 bible 数据 + 空 stats 兜底
        return _build_from_bible_only(novel_id, user_id, novel.get("linked_project_id"))

    screenplay_id = screenplay_record["id"]

    # 2. 拉 bible 五件套
    bible_data = _load_bible(novel_id)

    # 3. 解析 yaml
    try:
        parsed = yamllib.safe_load(screenplay_record["yaml_text"]) or {}
    except yamllib.YAMLError as e:
        logger.warning("character_profile: yaml parse failed for novel %s: %s", novel_id, e)
        parsed = {}

    yaml_characters = parsed.get("characters") or []
    yaml_scenes = parsed.get("scenes") or []

    # 4. 派生 stats
    stats_by_char_id = _derive_stats(yaml_characters, yaml_scenes)

    # 5. 桥接资产 — 收集角色名一次性查
    linked_project_id = novel.get("linked_project_id")
    bridge_assets_by_name: dict[str, CharacterBridgeAssets] = {}
    if linked_project_id:
        char_names = [
            c.get("name") for c in bible_data["characters"]
            if isinstance(c, dict) and c.get("name")
        ]
        bridge_assets_by_name = _load_bridge_assets(
            user_id, novel_id, char_names,
        )

    # 6. 组装 profiles
    bible_chars_by_id = {c["id"]: c for c in bible_data["characters"]}
    bible_chars_by_name = {c["name"]: c for c in bible_data["characters"]}

    # bible char_id ≠ yaml char_id(yaml 是 char_001 重发,bible 是 UUID hex)
    # 关键合并轴:**name**
    yaml_char_by_name = {c.get("name"): c for c in yaml_characters if isinstance(c, dict)}

    profiles: list[CharacterProfile] = []
    for bible_c in bible_data["characters"]:
        name = bible_c.get("name", "")
        if not name:
            continue
        yaml_c = yaml_char_by_name.get(name)
        yaml_char_id = yaml_c.get("id") if yaml_c else None
        stats = stats_by_char_id.get(yaml_char_id, CharacterStats()) if yaml_char_id else CharacterStats()

        # 戏份层级判定
        is_protagonist = bool(bible_c.get("is_protagonist"))
        if is_protagonist:
            stats.role_tier = "protagonist"
        elif stats.dialogue_count >= 5:
            stats.role_tier = "supporting"
        elif stats.dialogue_count > 0:
            stats.role_tier = "bit_part"
        else:
            stats.role_tier = "extra"

        # 该角色的关系(从 bible_relationships,以 bible char_id 为源)
        bible_id = bible_c["id"]
        relationships = []
        for r in bible_data["relationships"]:
            if r["source_char_id"] == bible_id:
                tgt = bible_chars_by_id.get(r["target_char_id"])
                if tgt:
                    relationships.append({
                        "target_id": r["target_char_id"],
                        "target_name": tgt["name"],
                        "type": r["type"],
                        "description": r["description"] or "",
                    })

        # 该角色参与的事件
        key_events = []
        for e in bible_data["events"]:
            if bible_id in e["participant_ids"]:
                key_events.append(CharacterKeyEvent(
                    description=e["description"],
                    chapter_number=e["chapter_number"],
                ))

        bridge = bridge_assets_by_name.get(name) if linked_project_id else None
        if bridge is not None and not bridge.has_any():
            bridge = None  # 全空就别展示 chips

        profiles.append(CharacterProfile(
            id=bible_id,
            name=name,
            aka=bible_c.get("aka", []),
            description=bible_c.get("description", ""),
            is_protagonist=is_protagonist,
            stats=stats,
            relationships=relationships,
            key_events=key_events,
            bridge_assets=bridge,
        ))

    # 7. 关系图 polarity(若 link)
    polarity_by_pair = (
        _load_polarity_for_pairs(user_id, novel_id, [(p.name, r["target_name"]) for p in profiles for r in p.relationships])
        if linked_project_id else {}
    )

    # 8. 组装 graph
    nodes = [
        GraphNode(
            id=p.id, name=p.name,
            role_tier=p.stats.role_tier,
            weight=max(1, p.stats.dialogue_count + p.stats.voiceover_count),
            is_protagonist=p.is_protagonist,
            has_bridge_assets=p.bridge_assets is not None,
        )
        for p in profiles
    ]
    edges: list[GraphEdge] = []
    for p in profiles:
        for r in p.relationships:
            polarity = polarity_by_pair.get((p.name, r["target_name"]))
            edges.append(GraphEdge(
                source=p.id,
                target=r["target_id"],
                type=r["type"],
                description=r["description"],
                polarity=polarity,
            ))

    return CharacterProfilesResult(
        novel_id=novel_id,
        screenplay_id=screenplay_id,
        characters=profiles,
        graph={
            "nodes": [asdict(n) for n in nodes],
            "edges": [asdict(e) for e in edges],
        },
        linked_project_id=linked_project_id,
    )


# ============================================================
# 内部:bible 数据加载
# ============================================================


def _load_bible(novel_id: str) -> dict:
    """从 sp_story_bibles 五件套拉数据(私有,调用方已校验归属)。"""
    conn = get_connection()
    try:
        bible_row = conn.execute(
            "SELECT id FROM sp_story_bibles WHERE novel_id = ?", (novel_id,),
        ).fetchone()
        if bible_row is None:
            return {"characters": [], "locations": [], "relationships": [], "events": []}
        bible_id = bible_row["id"]

        chars = [
            {
                "id": r["id"],
                "name": r["name"],
                "aka": json.loads(r["aka_json"] or "[]"),
                "description": r["description"] or "",
                "is_protagonist": bool(r["is_protagonist"]),
            }
            for r in conn.execute(
                """SELECT id, name, aka_json, description, is_protagonist
                     FROM sp_bible_characters WHERE bible_id = ? ORDER BY id""",
                (bible_id,),
            ).fetchall()
        ]
        rels = [
            {
                "id": r["id"],
                "source_char_id": r["source_char_id"],
                "target_char_id": r["target_char_id"],
                "type": r["type"],
                "description": r["description"] or "",
            }
            for r in conn.execute(
                """SELECT id, source_char_id, target_char_id, type, description
                     FROM sp_bible_relationships WHERE bible_id = ? ORDER BY id""",
                (bible_id,),
            ).fetchall()
        ]
        events = [
            {
                "id": r["id"],
                "description": r["description"],
                "chapter_number": r["chapter_number"],
                "participant_ids": json.loads(r["participant_ids_json"] or "[]"),
            }
            for r in conn.execute(
                """SELECT id, description, chapter_number, participant_ids_json
                     FROM sp_bible_events WHERE bible_id = ? ORDER BY id""",
                (bible_id,),
            ).fetchall()
        ]
        return {"characters": chars, "locations": [], "relationships": rels, "events": events}
    finally:
        conn.close()


# ============================================================
# 内部:从 yaml 派生 stats
# ============================================================


def _derive_stats(
    yaml_characters: list[dict],
    yaml_scenes: list[dict],
) -> dict[str, CharacterStats]:
    """每个 yaml char_id → CharacterStats。"""
    stats: dict[str, CharacterStats] = {
        c.get("id"): CharacterStats()
        for c in yaml_characters
        if isinstance(c, dict) and c.get("id")
    }

    if not stats:
        return {}

    # 一次扫 yaml_scenes,累加每个 char 的指标
    for scene in yaml_scenes:
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("id", "")
        scene_number = scene.get("number")
        chapter_no = (scene.get("source") or {}).get("chapter") if isinstance(scene.get("source"), dict) else None
        present = scene.get("characters_present") or []
        elements = scene.get("elements") or []

        # 对所有出场角色:scene_count + chapter(set) + first_appearance(if not set)
        # 用单独的 char→chapters set 累加
        for cid in present:
            if cid not in stats:
                continue
            s = stats[cid]
            s.scene_count += 1
            if s.first_appearance_scene is None:
                s.first_appearance_scene = scene_id
                if isinstance(scene_number, int):
                    s.first_appearance_number = scene_number
            # chapter_count 用 _chapter_set 暂存(下面合并)
            if not hasattr(s, "_chapter_set"):
                s._chapter_set = set()
            if isinstance(chapter_no, int):
                s._chapter_set.add(chapter_no)

        # 对每个 element,累加该角色的 dialogue / voiceover
        for el in elements:
            if not isinstance(el, dict):
                continue
            etype = el.get("type")
            cid = el.get("character_id")
            if cid not in stats:
                continue
            if etype == "dialogue":
                stats[cid].dialogue_count += 1
            elif etype == "voiceover":
                stats[cid].voiceover_count += 1

    # 收尾:把 _chapter_set 转 count + 删
    for cid, s in stats.items():
        cs = getattr(s, "_chapter_set", set())
        s.chapter_count = len(cs)
        if hasattr(s, "_chapter_set"):
            delattr(s, "_chapter_set")
    return stats


# ============================================================
# 内部:bridge 资产加载
# ============================================================


def _load_bridge_assets(
    user_id: str, novel_id: str, character_names: list[str],
) -> dict[str, CharacterBridgeAssets]:
    """逐角色拉 SP-2 driver + SP-4 snapshot。返回 name → CharacterBridgeAssets。

    异常隔离:任意子查询失败 → 该角色对应空 assets(不阻断别的角色)。
    """
    if not character_names:
        return {}
    out: dict[str, CharacterBridgeAssets] = {}

    conn = get_connection()
    try:
        # 一次查 sp_novels 拿 linked_project_id(也是双检)
        proj_row = conn.execute(
            "SELECT linked_project_id FROM sp_novels WHERE id=? AND user_id=?",
            (novel_id, user_id),
        ).fetchone()
        if proj_row is None or not proj_row["linked_project_id"]:
            return {}
        project_id = proj_row["linked_project_id"]

        placeholders = ",".join(["?"] * len(character_names))

        # SP-2 driver(父平台 characters 表)
        try:
            driver_rows = conn.execute(
                f"""SELECT name, surface_goal, deep_need, fatal_blind_spot,
                           arc_from_to, secret_json
                    FROM characters
                    WHERE project_id=? AND name IN ({placeholders})""",
                (project_id, *character_names),
            ).fetchall()
            for r in driver_rows:
                assets = out.setdefault(r["name"], CharacterBridgeAssets())
                assets.surface_goal = (r["surface_goal"] or "").strip() or None
                assets.deep_need = (r["deep_need"] or "").strip() or None
                assets.fatal_blind_spot = (r["fatal_blind_spot"] or "").strip() or None
                assets.arc_from_to = (r["arc_from_to"] or "").strip() or None
                secrets_raw = r["secret_json"]
                if secrets_raw:
                    try:
                        parsed_secrets = json.loads(secrets_raw)
                        if isinstance(parsed_secrets, list):
                            assets.secrets = [s for s in parsed_secrets if isinstance(s, dict)]
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as e:  # noqa: BLE001
            logger.warning("bridge driver load failed: %s", e)

        # SP-4 最新 snapshot(找该项目最近完成 sim)
        try:
            sim_row = conn.execute(
                "SELECT id FROM simulations WHERE project_id=? AND state='done' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if sim_row:
                sim_id = sim_row["id"]
                char_id_rows = conn.execute(
                    f"SELECT id, name FROM characters "
                    f"WHERE project_id=? AND name IN ({placeholders})",
                    (project_id, *character_names),
                ).fetchall()
                for cr in char_id_rows:
                    snap = conn.execute(
                        "SELECT position, hp_status, emotion_vec_json, inventory_json "
                        "FROM character_state_snapshots "
                        "WHERE simulation_id=? AND character_id=? "
                        "ORDER BY scene_index DESC LIMIT 1",
                        (sim_id, cr["id"]),
                    ).fetchone()
                    if snap is None:
                        continue
                    assets = out.setdefault(cr["name"], CharacterBridgeAssets())
                    assets.snapshot_position = snap["position"] or None
                    assets.snapshot_hp_status = snap["hp_status"] or None
                    if snap["emotion_vec_json"]:
                        try:
                            emo = json.loads(snap["emotion_vec_json"])
                            if isinstance(emo, dict) and emo:
                                # 主导情绪 top1
                                top_k, top_v = max(emo.items(), key=lambda kv: float(kv[1]))
                                assets.snapshot_emotion_top = f"{top_k}={float(top_v):.1f}"
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
                    if snap["inventory_json"]:
                        try:
                            inv = json.loads(snap["inventory_json"])
                            if isinstance(inv, list):
                                assets.snapshot_inventory = [str(x) for x in inv[:5]]
                        except (json.JSONDecodeError, TypeError):
                            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("bridge snapshot load failed: %s", e)
    finally:
        conn.close()

    return out


def _load_polarity_for_pairs(
    user_id: str, novel_id: str,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """查每对角色的 polarity(SP-7)。返 (source_name, target_name) → polarity。

    异常隔离:失败返空 dict。
    """
    if not pairs:
        return {}
    unique_names = list({n for p in pairs for n in p})
    out: dict[tuple[str, str], str] = {}
    conn = get_connection()
    try:
        proj_row = conn.execute(
            "SELECT linked_project_id FROM sp_novels WHERE id=? AND user_id=?",
            (novel_id, user_id),
        ).fetchone()
        if proj_row is None or not proj_row["linked_project_id"]:
            return {}
        project_id = proj_row["linked_project_id"]

        placeholders = ",".join(["?"] * len(unique_names))
        char_rows = conn.execute(
            f"SELECT id, name FROM characters "
            f"WHERE project_id=? AND name IN ({placeholders})",
            (project_id, *unique_names),
        ).fetchall()
        cid_by_name = {r["name"]: r["id"] for r in char_rows}

        char_ids = list(cid_by_name.values())
        if not char_ids:
            return {}
        id_placeholders = ",".join(["?"] * len(char_ids))
        rel_rows = conn.execute(
            f"SELECT source_id, target_id, polarity FROM relationships "
            f"WHERE project_id=? "
            f"  AND source_id IN ({id_placeholders}) "
            f"  AND target_id IN ({id_placeholders})",
            (project_id, *char_ids, *char_ids),
        ).fetchall()
        # 反查 name
        name_by_cid = {v: k for k, v in cid_by_name.items()}
        for r in rel_rows:
            src_name = name_by_cid.get(r["source_id"])
            tgt_name = name_by_cid.get(r["target_id"])
            polarity = r["polarity"]
            if src_name and tgt_name and polarity:
                out[(src_name, tgt_name)] = polarity
    except Exception as e:  # noqa: BLE001
        logger.warning("bridge polarity load failed: %s", e)
    finally:
        conn.close()
    return out


# ============================================================
# 兜底:无 screenplay 时仅返 bible 数据
# ============================================================


def _build_from_bible_only(
    novel_id: str, user_id: str, linked_project_id: Optional[str],
) -> CharacterProfilesResult:
    """剧本未生成时,只返 bible 信息 + 空 stats。让前端能在 compose 之前预览角色。"""
    bible_data = _load_bible(novel_id)
    char_names = [
        c.get("name") for c in bible_data["characters"]
        if isinstance(c, dict) and c.get("name")
    ]
    bridge_assets_by_name = (
        _load_bridge_assets(user_id, novel_id, char_names) if linked_project_id else {}
    )
    profiles: list[CharacterProfile] = []
    bible_chars_by_id = {c["id"]: c for c in bible_data["characters"]}

    for bible_c in bible_data["characters"]:
        name = bible_c.get("name", "")
        if not name:
            continue
        bible_id = bible_c["id"]
        relationships = []
        for r in bible_data["relationships"]:
            if r["source_char_id"] == bible_id:
                tgt = bible_chars_by_id.get(r["target_char_id"])
                if tgt:
                    relationships.append({
                        "target_id": r["target_char_id"],
                        "target_name": tgt["name"],
                        "type": r["type"],
                        "description": r["description"] or "",
                    })
        key_events = [
            CharacterKeyEvent(description=e["description"], chapter_number=e["chapter_number"])
            for e in bible_data["events"] if bible_id in e["participant_ids"]
        ]
        bridge = bridge_assets_by_name.get(name)
        if bridge is not None and not bridge.has_any():
            bridge = None

        stats = CharacterStats()
        stats.role_tier = "protagonist" if bible_c.get("is_protagonist") else "extra"

        profiles.append(CharacterProfile(
            id=bible_id, name=name, aka=bible_c.get("aka", []),
            description=bible_c.get("description", ""),
            is_protagonist=bool(bible_c.get("is_protagonist")),
            stats=stats, relationships=relationships, key_events=key_events,
            bridge_assets=bridge,
        ))

    polarity_by_pair = (
        _load_polarity_for_pairs(user_id, novel_id, [(p.name, r["target_name"]) for p in profiles for r in p.relationships])
        if linked_project_id else {}
    )

    nodes = [
        GraphNode(
            id=p.id, name=p.name, role_tier=p.stats.role_tier,
            weight=1, is_protagonist=p.is_protagonist,
            has_bridge_assets=p.bridge_assets is not None,
        )
        for p in profiles
    ]
    edges = []
    for p in profiles:
        for r in p.relationships:
            polarity = polarity_by_pair.get((p.name, r["target_name"]))
            edges.append(GraphEdge(
                source=p.id, target=r["target_id"], type=r["type"],
                description=r["description"], polarity=polarity,
            ))

    return CharacterProfilesResult(
        novel_id=novel_id, screenplay_id="",
        characters=profiles,
        graph={
            "nodes": [asdict(n) for n in nodes],
            "edges": [asdict(e) for e in edges],
        },
        linked_project_id=linked_project_id,
    )
