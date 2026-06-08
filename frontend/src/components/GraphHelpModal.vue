<script setup lang="ts">
/**
 * GraphHelpModal — 3D 关系图谱使用说明书(Sprint 1.M Polish)。
 *
 * 触发:ProjectGraphView 顶栏右上角 ? 按钮(FPS 旁边)
 *
 * 内容按"基础操作 → 关系连接 → AI 联动"分组,跟着用户实际从浅到深的学习曲线;
 * 每条只一行,带快捷键 chip + 一句话解释。不弹长文,不教条。
 *
 * Props:
 *   open  boolean
 *
 * Emits:
 *   close  用户关闭(× / Esc / 点 backdrop)
 */

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function handleKey(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}

interface HelpItem {
  /** 快捷键 / 触发动作 chip */
  trigger: string;
  /** 一句话说明 */
  desc: string;
}

interface HelpSection {
  title: string;
  items: HelpItem[];
}

const SECTIONS: HelpSection[] = [
  {
    title: "基础操作",
    items: [
      { trigger: "鼠标拖动", desc: "旋转 3D 视角" },
      { trigger: "滚轮", desc: "缩放" },
      { trigger: "右键拖动", desc: "平移视角" },
      { trigger: "点节点", desc: "右抽屉打开 — 编辑 5 字段(角色)/ 描述 + 参与角色(事件)" },
      { trigger: "拖节点", desc: "改位置 — 800ms 后自动保存,下次进入图谱位置保留" },
      { trigger: "右键空白", desc: "弹小菜单 — 新角色 / 新事件;不填名 Esc 关 = 不创建" },
    ],
  },
  {
    title: "关系连接",
    items: [
      { trigger: "Shift + 点角色 A", desc: "选关系起点(顶部紫色 hint bar 提示)" },
      { trigger: "Shift + 点角色 B", desc: "弹关系类型对话框(亲属 / 敌对 / 朋友 / 师徒...)" },
      { trigger: "Shift + 点同一角色两次", desc: "取消起点(无对话框)" },
      { trigger: "右键空白(已有起点时)", desc: "也能取消 Shift 起点,不弹菜单" },
    ],
  },
  {
    title: "AI 联动",
    items: [
      { trigger: "角色抽屉 → ✦ AI 对焦", desc: "单角色精修 — AI 给雷区草稿,逐条采纳 / 改一改 / 不采纳" },
      { trigger: "Ctrl/Cmd + 点角色", desc: "框选(可多个)— 底部浮动 bar 出按钮(≥2 启用),按钮文案随项目 mode 变(初始=AI 推演 / 中间=AI 重塑 / 末尾=AI 续写 / 周期=AI 长篇)" },
      { trigger: "点该按钮", desc: "弹推演 dock,自动预填「围绕 X / Y / Z 这几个角色…」让你接着写" },
      { trigger: "推演运行时", desc: "参与角色高亮 + 1.6s 周期脉搏;非参与者暗化让你聚焦" },
      { trigger: "创作完成后", desc: "参与角色稳态发光「光迹」;点节点抽屉显「✨ 跳详情」chip" },
    ],
  },
  {
    title: "诊断 + 重生成",
    items: [
      { trigger: "推演详情页 → ✦ 不满意?诊断", desc: "自洽守护者 8 维度审产物 vs 设定,免费(成本 ~0.05 元)" },
      { trigger: "诊断卡片「→ 去修」按钮", desc: "用户可修类(角色单薄 / 事件脱节 / 关系失真)直接跳编辑面" },
      { trigger: "诊断卡片「用建议续写一篇」", desc: "跳项目页打开 AI 续写,采纳的建议已自动预填到配置" },
    ],
  },
];
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="3D 图谱使用说明"
        @click="handleBackdrop"
        @keydown="handleKey"
      >
        <div class="modal-card surface">
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>

          <header class="modal-header">
            <h2 class="modal-title">✦ 3D 图谱使用说明</h2>
            <p class="modal-subtitle">
              "3D 即编辑器" — 不只是看世界,是操作世界
            </p>
          </header>

          <div class="sections">
            <section
              v-for="(sec, sIdx) in SECTIONS"
              :key="sIdx"
              class="help-section"
            >
              <h3 class="section-title">{{ sec.title }}</h3>
              <ul class="item-list">
                <li
                  v-for="(item, iIdx) in sec.items"
                  :key="iIdx"
                  class="item-row"
                >
                  <span class="item-trigger">{{ item.trigger }}</span>
                  <span class="item-arrow" aria-hidden="true">→</span>
                  <span class="item-desc">{{ item.desc }}</span>
                </li>
              </ul>
            </section>
          </div>

          <footer class="modal-footer">
            <p class="footer-hint">按 Esc 或点空白处关闭</p>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 640px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-7);
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
  background: transparent;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  margin-bottom: var(--space-5);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  margin-bottom: var(--space-1);
  color: var(--color-text);
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  font-style: italic;
}

.sections {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.section-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-accent-text);
  margin-bottom: var(--space-3);
}

.item-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  padding: 0;
  margin: 0;
}

.item-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.item-trigger {
  flex-shrink: 0;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  white-space: nowrap;
}

.item-arrow {
  color: var(--color-text-subtle);
  flex-shrink: 0;
}

.item-desc {
  color: var(--color-text);
  flex: 1;
}

.modal-footer {
  margin-top: var(--space-5);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
  text-align: center;
}
.footer-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

</style>
