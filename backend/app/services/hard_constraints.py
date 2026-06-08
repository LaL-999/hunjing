"""Sprint 6.A2 M5.3(2026-05-20)— Pre-generation Hard Constraints(生成前硬铁律 prepend)。

**M5 治本的核心机制**:把已确立的硬铁律(实体身份 / 已发生原子动作 / 物理位置 /
时间锚 / 角色情绪)在 narrator / agent_dialogue / scene_picker LLM 调用前,
**prepend 到 system_prompt 顶部**(LLM attention 权重最强的位置)。

为什么是 prepend 到顶部?
  - LLM 的 attention 机制对 system_prompt 顶部权重最高
  - M4 把 facts 放在 user_input 中段 → LLM 注意力被稀释 → "创作妥协"
  - M5 把硬铁律放在 system_prompt 顶部 → LLM 一开始就被锁死 → 自然合规

API:
  - build_hard_constraints_block(conn, sim, scene_index, agents) → str
    生成完整的硬铁律段(可能跨多行),caller prepend 到 system_prompt 顶部
  - prepend_constraints_to_prompt(system_prompt, constraints_block) → str
    简单字符串拼接的工具函数
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.models.character import Character
from app.models.simulation import Simulation

logger = logging.getLogger(__name__)


# 硬铁律段的边界标记(让 LLM 容易识别"以上是硬约束")
_BORDER = "=" * 70


def build_hard_constraints_block(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    agents: list[Character],
    *,
    skip_actions: bool = False,
    skip_emotions: bool = False,
) -> str:
    """生成本幕的硬铁律段,caller prepend 到 system_prompt 顶部。

    Args:
      sim: 当前 sim(必须 mode='evolution')
      scene_index: 本幕索引
      agents: 本幕在场角色(用于拉每人的情绪记录)
      skip_actions: True 时不拉 action_ledger(给 scene_picker 用,场景前不必约束动作)
      skip_emotions: True 时不拉 emotional_states

    Returns:
      硬铁律段(完整字符串,含边界标记;若所有维度都空 → 返空串)
    """
    sections: list[str] = []

    # 0.0 hotfix(2026-06-01)— 作品篇幅 → 细节颗粒度档(项目级)
    # 治 Gemini 评测点出"长篇每秒放慢镜头特写,读者会疲劳" — 让 narrator 按篇幅调密度
    if not skip_actions:  # 只 narrator/agent 阶段需要,scene_picker 阶段跳过
        try:
            from app.db import fetch_one as _fo_el
            proj_row = _fo_el(
                conn,
                "SELECT expected_length FROM projects WHERE id=?",
                (sim.project_id,),
            )
            el = "medium"
            if proj_row:
                try:
                    el_raw = proj_row["expected_length"]
                    if el_raw in ("short", "medium", "long"):
                        el = el_raw
                except (KeyError, IndexError):
                    pass
            if el == "short":
                sections.append(
                    "【细节颗粒度档:短篇 / 高密度 · 但必须多样化】\n"
                    "  这是单章 / 短篇作品,**每一段都要细致**,但**严禁反复用同一组动作**:\n"
                    "    ✓ 微表情(咬下唇 / 眉头一颤 / 视线躲闪 / 鼻翼微张 / 喉结滚 / 太阳穴跳)频繁出现\n"
                    "      **但**:同一种微表情全篇 ≤ 2 次,持续紧张时**轮换**用近义不同形式\n"
                    "      (例:第 1 次喉结滚 / 第 2 次咽口唾沫 / 第 3 次嗓子发紧 / 第 4 次干咽一下)\n"
                    "    ✓ 动作配音效(指节叩窗 / 拖鞋蹭地 / 抽绳晃动 / 烟蒂明灭 / 指甲剐书脊)\n"
                    "      **但**:同一动作全篇 ≤ 2 次,**绝不允许「指尖发抖 / 指节发白」每段都出现**\n"
                    "    ✓ 环境感官(冷意贴皮肤 / 走廊远处的水管声 / 屏幕反光 / 远处犬吠 / 楼道回响)\n"
                    "      **但**:同一意象(如「路灯昏黄 / 夜风卷起碎纸片」)全篇 ≤ 2 次\n"
                    "    ✗ **绝对禁止**:用同一组 3-4 个微表情/动作组合反复写 5+ 段对峙\n"
                    "      (这是常见质量灾难 — 短篇短了,意象只有几个,看起来反复像复读)\n"
                    "    ✓ 适合此档:高潮章 / 短篇 / 关键转折场 / 情绪密度高的对峙\n"
                )
            elif el == "long":
                sections.append(
                    "【细节颗粒度档:长篇 / 松弛】\n"
                    "  这是长篇作品,**不能每段都放慢镜头特写** — 读者长读会疲劳.\n"
                    "  本幕属于什么类型 → 决定密度:\n"
                    "    · 转折 / 高潮 / 关键对峙 / 情绪爆点 → 用短篇式高密度(微表情 + 音效 + 感官齐全)\n"
                    "    · 常规场景 / 过场 / 信息交代 / 日常互动 → **明显松弛**:\n"
                    "      ✗ 不必每段配微表情(可只在关键句给一个)\n"
                    "      ✗ 不必每个动作配音效(只在画面感强的地方给)\n"
                    "      ✗ 不必密集环境感官描写(开篇定个场,中间靠对白和情节推进)\n"
                    "      ✓ 多用一句话带过的简洁描述(例:'两人沉默地走完了那段路.')\n"
                    "      ✓ 多用语义概括而非分镜:'她整理了一下情绪' 而非 '她吸气-呼气-眨眼-理鬓发'\n"
                    "    · 判断:不确定时 → 偏松弛档(宁可松也不要过度紧绷)\n"
                )
            else:  # medium 默认
                sections.append(
                    "【细节颗粒度档:中篇 / 平衡】\n"
                    "  本幕密度按'七分密度'控制:\n"
                    "    · 核心场景(主角与关键角色的关键对话 / 转折点)→ 用高密度(微表情 + 音效 + 感官)\n"
                    "    · 过场 / 信息交代 / 配角互动 → 偏简略(只在画面感强处加细节)\n"
                    "    · 不要每段都细致 — 留点呼吸空间给读者\n"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"hard_constraints: expected_length block failed: {e}")

    # 0.05(2026-06-01)— 章节自觉(让 AI 知道当前章序号但禁止写显式章标题)
    # 治"滚雪球章号断层"+"narrator 写出第N章污染读者端切章"
    if not skip_actions:  # 只 narrator/agent 阶段需要,scene_picker 阶段跳过
        try:
            from app.services.sim_chapter_helper import (
                _get_chapter_size_range_for_project,
                get_effective_start_chapter,
            )
            start_chap = get_effective_start_chapter(sim)
            cmin, cmax = _get_chapter_size_range_for_project(conn, sim.project_id)
            cur_narr_len = len(sim.narrative or "")
            avg = (cmin + cmax) // 2
            chapters_done_in_sim = cur_narr_len // max(1, avg)
            current_chapter = start_chap + chapters_done_in_sim
            sections.append(
                f"【章节进度】当前约第 {current_chapter} 章 / 每章 {cmin}~{cmax} 字 / "
                "禁止写出 第N章 标题或 # ## 分章符(章切由读者端做).\n"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"hard_constraints: chapter awareness block failed: {e}")

    # 0.0 SP-1(2026-05-28)— 故事内核三件套(灵魂续写北极星·目的层)
    # 最高优先级:比物理 / 时间 / 实体身份 还上层 — 它是"为什么写这个故事"
    # 治"LLM 没目标弧就提前泄气" — 给推演一个目标终点,它才敢憋住不解决
    try:
        from app.services.story_core_util import build_story_core_block
        # 进度提示参数:scene_index 0-based + outline 总幕数(若有)
        total_planned = None
        try:
            from app.db import fetch_one as _fo
            r = _fo(
                conn,
                "SELECT COUNT(*) AS c FROM outline_scenes "
                "WHERE simulation_id=?",
                (sim.id,),
            )
            total_planned = int(r["c"]) if r and r["c"] else None
        except Exception as e:  # noqa: BLE001
            # 2026-06-02 批次 2:silent failure 加 log(老库无 outline_scenes 表时降级)
            logger.debug(f"hard_constraints: outline_scenes count failed sim={sim.id}: {e}")
            total_planned = None
        sc_block = build_story_core_block(
            conn, sim.project_id,
            current_scene_index=scene_index,
            total_scenes_planned=total_planned,
        )
        if sc_block:
            sections.append(sc_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: story_core block failed: {e}")

    # 0. P1.B(2026-05-24)— 项目级角色生命/物理状态铁律
    # P0H.1(2026-05-24)增强:unknown 也注入软约束,治"AI 误判 alive → 死者复活"
    # 治"已死角色复活"瑕疵:挪威森林续作里直子(在阿美寮疗养院/后自杀)与绿子在阁楼同框,
    # 完全违反原作时间线。需先于"实体身份锁定"注入,作为最高优先级铁律。
    # 普适性:任何作品都需要 — 三体里"叶文洁(已被捕入狱)"、红楼"林黛玉(已死)"
    try:
        from app.db import fetch_all
        char_rows = fetch_all(
            conn,
            "SELECT name, life_status, status_note FROM characters "
            "WHERE project_id=? AND life_status != 'alive'",
            (sim.project_id,),
        )
        deceased = [r for r in char_rows if r["life_status"] == "deceased"]
        absent_or_facility = [
            r for r in char_rows
            if r["life_status"] in ("in_facility", "absent")
        ]
        unknown_chars = [r for r in char_rows if r["life_status"] == "unknown"]

        if deceased or absent_or_facility or unknown_chars:
            lines = ["【项目级角色状态锁定(本幕硬约束)】"]

            if deceased:
                lines.append("  ⚰ 以下角色**已死亡 / 不在世**,只可以**作为客体或回忆中**出现,不可像活人一样行动:")
                for r in deceased:
                    note = f"({r['status_note']})" if r["status_note"] else ""
                    lines.append(f"     - {r['name']} {note}")
                lines.append(
                    "    死者**唯一允许的出场方式**(P0P 精细化,2026-05-24):\n"
                    "      ✓ 葬礼 / 守灵 / 出殡场景中作为被悼念的对象\n"
                    "      ✓ 死亡刚发生时的**尸体处理 / 善后**(被搬运 / 被哭悼 / 被处理后事)\n"
                    "      ✓ 他人的**回忆 / 梦境 / 提及姓名**(间接出现)\n"
                    "      ✓ 遗物 / 遗书 / 墓地 / 牌位 作为剧情线索\n"
                    "    \n"
                    "    死者**绝对禁止**的写法(违反 = 整次产出作废):\n"
                    "      ✗ 死者**说话** — 任何对白 / 嘴唇翕动 / 喃喃自语\n"
                    "      ✗ 死者**主动行动** — 走路 / 看 / 想 / 笑 / 哭 / 推门 / 坐下 等具体动作\n"
                    "      ✓ 例外:'回忆/梦境'内的死者动作合法(必须用引导词显式标记:'她想起<死者>那时...')\n"
                    "      ✗ 死者**有内心活动** — '她心想…' / '他感到…' / '她意识到…'\n"
                    "      ✗ 死者**主动加入主线场景** — 突然出现在街上 / 推门进来 / 主动找主角\n"
                    "      ✗ 时间过去很久后,死者**像活人一样出场** — 比如直子死后 1 年还在书店和绿子聊天\n"
                    "    \n"
                    "    简单判定:**死者只能是被叙述的客体,不能是叙述的主语**。\n"
                    "      ✓ 客体(被动):'驹子怀里抱着叶子的尸体' / '叶子的灵柩' / '叶子的弟弟跪在叶子身旁'\n"
                    "      ✗ 主语(主动):'叶子静静地看着窗外' / '叶子说,你来了' / '叶子想着家人'"
                )

            if absent_or_facility:
                lines.append("  ⚠ 以下角色**不在主线物理在场**(在异地 / 在机构),除非主角到该地点否则不应同框:")
                for r in absent_or_facility:
                    status_tag = "在特定地点" if r["life_status"] == "in_facility" else "暂时离开"
                    note = f"({r['status_note']})" if r["status_note"] else ""
                    lines.append(f"     - {r['name']} [{status_tag}] {note}")
                lines.append(
                    "    铁律:仅当本幕场景显式标注'主角去到该角色所在地'时,他们才可在场;"
                    "否则只能通过来信 / 电话 / 他人转述间接出现。"
                )

            if unknown_chars:
                lines.append("  ⚠ 以下角色**状态未知**(AI 抽取时无足够证据判定;可能已死/异地/离开):")
                for r in unknown_chars:
                    note = f"({r['status_note']})" if r["status_note"] else ""
                    lines.append(f"     - {r['name']} {note}")
                lines.append(
                    "    软铁律(P0H.1):谨慎让这些角色物理在场 — 若续作让其在主线出现,"
                    "必须给出**明确铺垫**(主角去找他/接到他的消息/他归来等过渡)。"
                    "不许直接让 unknown 角色凭空出现在某场景里。"
                )

            sections.append("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: life-status query failed: {e}")

    # 0.5 P0V.1 / P0X.2(2026-05-26)— 物理 / 语言能力锁定
    # 起源:13574/14345/15070/15365 四份续作里同一个"半身不遂"师傅产生 4 种不同行为。
    # P0X.2 把翻译规则抽到 physical_constraints_util 共享 module,
    # evolution + quick 模式都用同一份铁律,杜绝 LLM 每次"重新解读"。
    try:
        from app.services.physical_constraints_util import (
            build_physical_constraints_block,
        )
        block = build_physical_constraints_block(conn, sim.project_id)
        if block:
            sections.append(block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: physical_constraints block failed: {e}")

    # 1. 实体身份锁定(canonical_entities)+ 出场状态机(M11.D)
    try:
        from app.services.entity_registrar import list_canonical_entities
        from app.services.action_extractor import count_actor_appearances
        entities = list_canonical_entities(conn, sim.id)
        if entities:
            lines = ["【已锁定的核心实体身份(严禁覆盖)】"]
            # 取前 20 个,避免 prompt 过长
            for e in entities[:20]:
                # M11.D(2026-05-24):character 实体追加"出场状态",
                # 治"神秘女孩两次第一次见面"瑕疵 — 第二次出场必须以"再见"姿态出场,
                # 不许重复初次见面的描写(气味识别 / 自我介绍 / 重新建立身份)
                extra = ""
                if e.entity_type == "character":
                    appearances = count_actor_appearances(
                        conn, sim.id, e.canonical_name,
                    )
                    if e.first_introduced_scene < scene_index:
                        # 本幕之前已出场过
                        if appearances >= 2:
                            extra = (
                                f"  ⚡ 已出场 {appearances} 次(首见于第 "
                                f"{e.first_introduced_scene + 1} 幕)— 本幕若再次出场,"
                                "**严禁重复初次见面桥段**(气味识别/自我介绍/初次建立身份等)"
                            )
                        else:
                            extra = (
                                f"  ⚡ 已在第 {e.first_introduced_scene + 1} 幕首次出场 — "
                                "本幕若再次出场是**再相见**,不是初见"
                            )
                lines.append(f"  - {e.to_prompt_line()}")
                if extra:
                    lines.append(extra)
            lines.append(
                "  ⚠ 铁律:这些实体的身份已锁定,后续叙事必须使用 canonical_name 或 "
                "已注册的 aliases;**严禁造出语义重合的新身份**(如已有'林小满'就不许造'林小禾')。"
            )
            sections.append("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: list_canonical_entities failed: {e}")

    # 2. 已发生不可重复原子动作(action_ledger)
    if not skip_actions:
        try:
            from app.services.action_extractor import list_atomic_actions
            atomic_actions = list_atomic_actions(conn, sim.id)
            if atomic_actions:
                lines = ["【已发生的不可重复原子动作(本幕严禁再次发生)】"]
                # 取最近的 15 条,避免 prompt 过载
                for a in atomic_actions[-15:]:
                    lines.append(f"  - {a.to_prompt_line()}")
                lines.append(
                    "  ⚠ 铁律:以上原子动作已落账,本幕**不许重复**(如已'抽出照片'就不许再写'抽出照片'),"
                    "可以承接后续动作(如基于已抽出的照片做新事 —— 烧掉/撕碎/递给他人)。"
                )
                sections.append("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"hard_constraints: list_atomic_actions failed: {e}")

    # 1.5 SP-2(2026-05-28)— 角色驱动力(灵魂续写北极星·驱动层)
    # 让本幕在场角色从被动反应升级为主动 agent — goal vs need 张力 + 秘密 + 弧光
    try:
        from app.services.character_drivers_util import build_character_drivers_block
        agent_names = [a.name for a in agents if a and a.name]
        if agent_names:
            cd_block = build_character_drivers_block(
                conn, sim.project_id, agent_names,
            )
            if cd_block:
                sections.append(cd_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: character_drivers block failed: {e}")

    # 1.6 SP-3(2026-05-28)— 知识边界(信息不对称层,治 AI 最大连贯 bug)
    # 每个在场角色"已知 / 未知"清单 + 铁律:不许角色用他不知道的信息
    try:
        from app.services.character_knowledge_util import build_knowledge_block
        agent_names_for_knowledge = [a.name for a in agents if a and a.name]
        if agent_names_for_knowledge:
            kn_block = build_knowledge_block(
                conn, sim.project_id, agent_names_for_knowledge,
                current_scene_index=scene_index,
            )
            if kn_block:
                sections.append(kn_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: knowledge block failed: {e}")

    # 1.7 Patch E(2026-06-02)— 明示陌生关系(治"角色关系闪现")
    # 项目内任意两角色 × 未在 relationships 表声明关系 → 自动视为"互不相识"
    # 治 LLM 看到多女主在场就假设她们认识(如刘欣悦上来叫张静怡为"静怡姐")
    try:
        from app.services.relationship_negative_util import build_unfamiliar_pairs_block
        unf_block = build_unfamiliar_pairs_block(conn, sim.project_id)
        if unf_block:
            sections.append(unf_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: unfamiliar pairs block failed: {e}")

    # 2.5 道具消费饱和冷却(M11.A,2026-05-24;M11.F.3 阈值自适应,2026-05-24)
    # 治"道具锁死"瑕疵:即便单条动作是 is_repeatable=1(可重复,如"反复摸某物"),
    # 同一 actor+verb+object 三元组在最近 N 幕重复 ≥ 阈值就该饱和。
    # 雪国里"雪"反复出现 OK,但每次消费方式不同(看/听/触/被打湿);
    # 续作里同一三元组重复 10+ 次 = AI 注意力锁死,**必须打破**。
    #
    # M11.F.3 自适应阈值(按 sim.target_chars 动态调):
    #   - 短篇(< 2000 字):阈值 2,快节奏更严格,不留 LLM 走神空间
    #   - 中篇(2000-5000 字):阈值 3(默认)
    #   - 长篇(> 5000 字):阈值 4,文学性留白多,允许稍多重复
    # 普适性:任何作品风格(物哀 / 硬科幻 / 武侠 / 古风)都能用同一规则,只是字数档不同
    if not skip_actions:
        try:
            from app.services.action_extractor import list_saturated_prop_usages
            target_chars = getattr(sim, "target_chars", 4000) or 4000
            if target_chars < 2000:
                threshold = 2
            elif target_chars > 5000:
                threshold = 4
            else:
                threshold = 3
            # 看过去 5 幕(含本幕)
            window_start = max(0, scene_index - 5)
            saturated = list_saturated_prop_usages(
                conn, sim.id,
                since_scene_index=window_start,
                threshold=threshold,
            )
            if saturated:
                lines = ["【道具消费饱和冷却(本幕严禁延续这些动作模式)】"]
                for s in saturated[:10]:  # 最多展示 10 条
                    obj_part = f" {s['object']}" if s["object"] else ""
                    lines.append(
                        f"  - {s['actor']} {s['verb']}{obj_part} — 过去 "
                        f"{scene_index - window_start + 1} 幕内已重复 {s['count']} 次"
                    )
                lines.append(
                    "  ⚠ 铁律:以上 (actor, verb, object) 三元组**已饱和**,本幕禁止再写该用法。"
                    "若要继续用此道具/动作,**必须换 verb 或换 object**。通用化示例:\n"
                    "    - 文学小说:反复'摸 某随身物' → 改'撕碎 / 凝视 / 嗅闻 / 念叨其上的字'\n"
                    "    - 科幻题材:反复'盯 某显示屏' → 改'调参数 / 关掉 / 砸碎 / 在屏上写指令'\n"
                    "    - 武侠题材:反复'握 某兵器' → 改'抽出半寸 / 入鞘 / 用鞘磕地 / 递给对方'\n"
                    "    - 现实题材:反复'看 某物件' → 改'拿起把玩 / 收进抽屉 / 放回原处'\n"
                    "  核心:**同一物理动作不许连续 3 次以上**,无论什么作品风格。"
                )
                sections.append("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"hard_constraints: list_saturated_prop_usages failed: {e}")

    # 2.6 P3 作者指南针(2026-05-26)— 作者风格基线
    # 由 author_compass_service 双轨 LLM 调研得出(外部研究 + 内部反推),
    # 用户审阅后锁定;evolution + quick 双模式共用此 prompt 段
    try:
        from app.services.author_compass_util import build_author_compass_block
        ac_block = build_author_compass_block(conn, sim.project_id)
        if ac_block:
            sections.append(ac_block)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: author_compass block failed: {e}")

    # 3. 时间链锁(temporal_lock)
    try:
        from app.services.temporal_lock import (
            format_temporal_constraint_line,
            get_last_time_offset_for_sim,
        )
        last_offset, last_anchor = get_last_time_offset_for_sim(conn, sim.id)
        if last_anchor:
            line = format_temporal_constraint_line(last_offset, last_anchor)
            if line:
                sections.append(f"【时间链锁】\n  - {line}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: temporal_lock failed: {e}")

    # 4. 角色情绪链(上幕末情绪 → 本幕开头基线)
    if not skip_emotions:
        try:
            from app.services.emotional_state_tracker import (
                list_latest_emotional_states_for_scene,
            )
            prev_states = list_latest_emotional_states_for_scene(
                conn, sim.id, scene_index, agents,
            )
            if prev_states:
                lines = ["【在场角色上幕末情绪(本幕必须自然承接 ±2 档内,突变 ≥5 必须有剧情铺垫)】"]
                for a in agents:
                    st = prev_states.get(a.id)
                    if st is not None:
                        lines.append(f"  - {st.to_prompt_line(character_name=a.name)}")
                lines.append(
                    "  ⚠ 铁律:角色情绪不允许突变(如 fear=9 → joy=8 直接跳跃 = 角色撕裂),"
                    "本幕剧情若有重大转折必须显式铺垫情绪转移,否则保持上幕基线 ±2 档内。"
                )
                sections.append("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"hard_constraints: emotional_states failed: {e}")

    # P2.A / P2.B(2026-05-24)— 用户级偏好铁律(创作配置层,与"治瑕疵"铁律平级)
    #
    # P2.A 走向终章开关:
    #   sim.with_grand_finale=0 → 禁止"主角告别过去/接受未来/做出人生重大决定"类大结局动作
    #   sim.with_grand_finale=1 → 鼓励主线收束 / 情绪闭环 / 角色弧光完成
    #
    # P2.B 叙事节奏档位 + 主动制造波折:
    #   slow     允许多幕同场景 + 鼓励氛围铺陈
    #   standard 默认 — 当前行为
    #   fast     紧凑:每 2-3 幕必须切场景 + 必须有外部刺激
    try:
        user_pref_lines: list[str] = []
        grand_finale = getattr(sim, "with_grand_finale", 0)
        pacing = getattr(sim, "narrative_pacing", "standard") or "standard"

        # --- 走向终章 ---
        if not grand_finale:
            user_pref_lines.append(
                "  ✗ **禁止大结局倾向**:本续作非终章产物 — 用户后续会继续创作。\n"
                "    严禁安排主角做以下任一类「大结局动作」:\n"
                "      - 与过去切断 / 告别所有逝者(如「我决定不再想她」)\n"
                "      - 接受未来路径 / 做出人生重大决定(如「我决定明天去找绿子」)\n"
                "      - 主动表白 / 求婚 / 立誓(「我想和你一起从头开始」类台词)\n"
                "      - 主线收束 / 弧光完成的「温暖收尾」\n"
                "    \n"
                "    **特别针对末尾几幕**(2026-06-02 Patch D 强化):\n"
                "    即使本幕是 outline 中的最后 1-3 幕,也**绝对禁止**:\n"
                "      ✗ 主要矛盾**集中收束**(多线同时告别 / 多个关系同时结束)\n"
                "      ✗ 多个角色**同时离场**(如「分手 / 告别 / 各方告别 / 角色弧光闭合」)\n"
                "      ✗ **总结式叙述**(如「这就是 X 与 Y 的故事的结尾」/「至此一切尘埃落定」)\n"
                "      ✗ **遗憾感 / 释然感 / 圆满感**收尾(任一种「完成」的氛围)\n"
                "      ✗ 主角**夜深躺床盯天花板**类「孤独中点开思考人生」收尾(常见 LLM 滥用收束镜头)\n"
                "      ✗ 主角**关掉手机 / 关掉电脑 / 拉黑某人**类「主动断联」动作\n"
                "    \n"
                "    本幕应保持**剧情开放性** — 主角仍在挣扎,矛盾未解,人物关系仍有发展空间。\n"
                "    末尾幕的正确处理:**留个新钩子 / 抛个新悬念 / 引入新变量** — 而不是回望式收束。"
            )
        else:
            user_pref_lines.append(
                "  ★ **走向终章**:本续作用户已勾选'大结局收尾',鼓励:\n"
                "      - 主线收束 — 主要悬念给出答复\n"
                "      - 情绪闭环 — 主角与重要他者关系达成阶段性平衡\n"
                "      - 弧光完成 — 主角心理曲线从开篇到此幕有可读的成长 / 蜕变"
            )

        # --- 叙事节奏 ---
        if pacing == "slow":
            user_pref_lines.append(
                "  🐢 **慢节奏**:允许多幕同场景 + 鼓励氛围铺陈(感官 / 留白 / 环境)。"
                "本幕**对白可少**,环境与心理细节占比可高。"
            )
        elif pacing == "fast":
            user_pref_lines.append(
                "  ⚡ **紧凑节奏**:本幕**必须**包含以下至少 1 项波折:\n"
                "      - 新角色登场(他者外部介入,非纯三人小圈子)\n"
                "      - 突发事件(意外消息 / 物理冲突 / 信件到来 / 第三方造访)\n"
                "      - 主线推进(plot_threads 中悬而未决的事件向前一步)\n"
                "    严禁连续 3 幕以上在\"喝茶聊天 / 看雪 / 翻杂志\"类静态场景里转悠。"
            )
        # standard 不加约束(向后兼容)

        if user_pref_lines:
            sections.append("【用户创作偏好(本 sim 配置)】\n" + "\n".join(user_pref_lines))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"hard_constraints: P2.A/P2.B user pref failed: {e}")

    if not sections:
        return ""

    # 包装边界 + 顶部声明
    header = (
        "\n" + _BORDER + "\n"
        "本幕硬铁律(违反 = 产物作废,平台会触发重写)\n"
        + _BORDER + "\n\n"
    )
    footer = "\n\n" + _BORDER + "\n以上是硬约束。以下是原 prompt:\n" + _BORDER + "\n\n"
    body = "\n\n".join(sections)
    return header + body + footer


def prepend_constraints_to_prompt(
    original_system_prompt: str,
    constraints_block: str,
) -> str:
    """把硬铁律段 prepend 到原 system_prompt 顶部。

    若 constraints_block 为空 → 返回原 prompt 不变(无副作用)。
    """
    if not constraints_block:
        return original_system_prompt
    return constraints_block + original_system_prompt
