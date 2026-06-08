<script setup lang="ts">
/**
 * Icon — 轻量 SVG 图标组件(lucide-style outline)
 *
 * 设计原则:
 *   - 单色 stroke 风格,与项目浅色 token 体系一致
 *   - currentColor 让外层 CSS 控制颜色(用 color 而非 fill)
 *   - viewBox 统一 24x24,size 默认 16(行内文本对齐)+ stroke-width 2
 *   - 0 依赖,path data 内嵌(避免新依赖触发架构冻结闸门)
 *
 * 用法:
 *   <Icon name="world" />
 *   <Icon name="character" :size="20" />
 *   <Icon name="edit" :size="16" class="my-icon-color" />
 *
 * 加新图标:
 *   1. 找一个 lucide-style outline 24x24 SVG path
 *   2. 加入下方 ICON_PATHS 字典
 *   3. 名字用语义命名(world / scene / character / event,不要 globe / pin / users / calendar)
 */
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    name: string;
    size?: number | string;
    strokeWidth?: number;
    /** filled=true 让 path 实心填充(适用于 star / heart 等需要"被点亮"语义)*/
    filled?: boolean;
  }>(),
  {
    size: 16,
    strokeWidth: 1.5,   // 用户反馈 2 偏粗显憨,1.5 更优雅
    filled: false,
  },
);

/**
 * 图标 path 字典 — 全部 24x24 viewBox,lucide-style outline。
 * 加新图标时按字母序插入,保持可维护。
 */
const ICON_PATHS: Record<string, string> = {
  // 世界观 — globe(地球轮廓 + 经纬线)
  world:
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M2 12h20 M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",

  // 场景图谱 — map-pin(定位针)
  scene:
    "M20 10c0 7-8 13-8 13s-8-6-8-13a8 8 0 0 1 16 0z M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",

  // 角色 — users(两个人形 silhouette)
  character:
    "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M23 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",

  // 事件 — calendar(日历框 + 头部双圆点)
  event:
    "M19 4H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z M16 2v4 M8 2v4 M3 10h18",

  // 编辑 — pencil(铅笔斜线)
  edit:
    "M12 20h9 M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z",

  // 关闭 — x
  close: "M18 6 6 18 M6 6l12 12",

  // 删除 — trash
  trash:
    "M3 6h18 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M10 11v6 M14 11v6",

  // 时钟 / phase / 时间 — clock
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 6v6l4 2",

  // 锁定 / 项目锁定 — lock(挂锁:锁体 + U 形挂钩)
  lock:
    "M5 11h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z M8 11V7a4 4 0 0 1 8 0v4",

  // 设置 / 自检 — settings(齿轮)
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z",

  // 对话 / 台词 — message-circle(气泡)
  message:
    "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z",

  // 禁止 / 雷区 — ban
  ban: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M4.93 4.93l14.14 14.14",

  // 主角星 — star(可选 filled=true 实心高亮 / 默认描边)
  star: "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z",

  // 性格 — sparkles(灵感火花,2 个小星,与 character icon 区分)
  sparkles:
    "M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z M19 14l.75 2.25L22 17l-2.25.75L19 20l-.75-2.25L16 17l2.25-.75L19 14z",

  // 加号
  plus: "M12 5v14 M5 12h14",

  // 右箭头
  arrow_right: "M5 12h14 M12 5l7 7-7 7",

  // 关系箭头 outbound(只箭头)
  arrow: "M5 12h14 M12 5l7 7-7 7",

  // 关系箭头 inbound(反向)
  arrow_left: "M19 12H5 M12 19l-7-7 7-7",

  // SP-2(2026-05-28)— 指南针(角色驱动 / 灵魂续写)
  compass:
    "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M16.24 7.76l-2.12 6.36-6.36 2.12 2.12-6.36 6.36-2.12z",

  // SP-3(2026-05-28)— 书 / 卷轴(知识边界 / 事实)
  book:
    "M4 19.5A2.5 2.5 0 0 1 6.5 17H20 M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z",

  // SP-3(2026-05-28)— 钥匙(角色知道某事实)
  key:
    "M21 2l-9.6 9.6 M15.5 7.5l3 3 M11.4 11.4a5 5 0 1 1-7 7 5 5 0 0 1 7-7z",

  // SP-7(2026-05-28)— 盾牌(关系正负 / 立场)
  shield:
    "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",

  // SP-8(2026-05-28)— 眼睛(视角)
  eye:
    "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",

  // 2026-06-01:汉堡(章节跳转入口)
  menu:
    "M3 12h18 M3 6h18 M3 18h18",

  // 2026-06-02:月亮(主题切换 — 夜间模式)
  moon:
    "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z",

  // 2026-06-02:文件(用户协议)
  file_text:
    "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M16 13H8 M16 17H8 M10 9H8",

  // 2026-06-02:退出(arrow + door)
  log_out:
    "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4 M16 17l5-5-5-5 M21 12H9",

  // 2026-06-01:书本展开(最终作品 badge)
  book_open:
    "M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z",
};

const pathD = computed(() => ICON_PATHS[props.name] ?? "");
const isUnknown = computed(() => !(props.name in ICON_PATHS));

if (import.meta.env.DEV && isUnknown.value) {
  // 开发期警告:用未注册图标名
  console.warn(`[Icon] unknown icon name: "${props.name}"`);
}
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    :fill="filled ? 'currentColor' : 'none'"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    class="huimeng-icon"
    :aria-hidden="true"
  >
    <path :d="pathD" />
  </svg>
</template>

<style scoped>
.huimeng-icon {
  flex-shrink: 0;
  display: inline-block;
  vertical-align: middle;
}
</style>
