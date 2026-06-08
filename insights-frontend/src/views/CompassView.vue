<script setup lang="ts">
/**
 * CompassView — B7 作者指南针使用看板(INS-B Phase 1,2026-05-27 末⁵).
 *
 * 数据源:huimeng.author_compass / huimeng.projects
 */
import { onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

interface CompassResp {
  huimeng_attached: boolean;
  usage: {
    projects_total: number;
    compass_total: number;
    usage_rate: number;
  } | null;
  lock_status: { locked: number; unlocked: number } | null;
  external_status: { status: string; count: number }[];
  internal_status: { status: string; count: number }[];
  top_authors: { name: string; count: number }[];
  top_works: { title: string; count: number }[];
}

const data = ref<CompassResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<CompassResp>("/admin/compass");
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

const STATUS_LABELS: Record<string, string> = {
  pending: "待开始",
  running: "运行中",
  done: "完成",
  failed: "失败",
};
</script>

<template>
  <section>
    <PageHero
      icon="compass"
      title="作者指南针"
      description="灵魂续写功能(P3 作者指南针)使用情况 — 多少项目用了 / 锁定率多高 / 用户最爱研究哪些作家。决定要不要给该功能加资源 / 改提示词。"
      audience="产品视角"
    />

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接,无法读 author_compass
    </div>

    <div v-if="data && data.huimeng_attached">
      <!-- 使用率 -->
      <div class="block">
        <div class="block-head">
          <Icon name="bar-chart-3" :size="14" />
          <h3>使用率</h3>
        </div>
        <div v-if="data.usage" class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ data.usage.projects_total }}</div>
            <div class="num-label">项目总数</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ data.usage.compass_total }}</div>
            <div class="num-label">用了指南针</div>
          </div>
          <div class="num-item accent">
            <div class="num-val">{{ (data.usage.usage_rate * 100).toFixed(1) }}%</div>
            <div class="num-label">使用率</div>
          </div>
        </div>
      </div>

      <!-- 锁定状态 -->
      <div class="block" v-if="data.lock_status">
        <div class="block-head">
          <Icon name="check" :size="14" />
          <h3>用户锁定</h3>
        </div>
        <div class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ data.lock_status.locked }}</div>
            <div class="num-label">已锁定(用户拍板版本)</div>
          </div>
          <div class="num-item">
            <div class="num-val muted-num">{{ data.lock_status.unlocked }}</div>
            <div class="num-label">未锁定(用 LLM 合并)</div>
          </div>
        </div>
      </div>

      <!-- 双轨完成状态 -->
      <div class="block">
        <div class="block-head">
          <Icon name="refresh" :size="14" />
          <h3>双轨状态分布</h3>
        </div>
        <div class="dual-grid">
          <div>
            <h4 class="sub-h">外部研究轨</h4>
            <ul class="status-list">
              <li v-for="s in data.external_status" :key="s.status">
                <span>{{ STATUS_LABELS[s.status] || s.status }}</span>
                <b>{{ s.count }}</b>
              </li>
            </ul>
          </div>
          <div>
            <h4 class="sub-h">内部反推轨</h4>
            <ul class="status-list">
              <li v-for="s in data.internal_status" :key="s.status">
                <span>{{ STATUS_LABELS[s.status] || s.status }}</span>
                <b>{{ s.count }}</b>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- top 10 作家 / 作品 -->
      <div class="dual-grid">
        <div class="block">
          <div class="block-head">
            <Icon name="user" :size="14" />
            <h3>top 10 研究最多的作家</h3>
          </div>
          <table v-if="data.top_authors.length" class="data-table">
            <tr v-for="a in data.top_authors" :key="a.name">
              <td>{{ a.name }}</td>
              <td class="r"><b>{{ a.count }}</b></td>
            </tr>
          </table>
          <div v-else class="empty">暂无数据</div>
        </div>

        <div class="block">
          <div class="block-head">
            <Icon name="book-open" :size="14" />
            <h3>top 10 研究最多的作品</h3>
          </div>
          <table v-if="data.top_works.length" class="data-table">
            <tr v-for="w in data.top_works" :key="w.title">
              <td>{{ w.title }}</td>
              <td class="r"><b>{{ w.count }}</b></td>
            </tr>
          </table>
          <div v-else class="empty">暂无数据</div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.warn { color: #92400e; padding: 0.75rem; background: #fef3c7; border-radius: 0.375rem; margin-bottom: 1rem; font-size: 0.8125rem; }
.empty { color: #9a968d; font-style: italic; padding: 1.5rem; text-align: center; font-size: 0.8125rem; }

.block {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
}
.block-head { display: flex; align-items: center; gap: 0.5rem; color: #6b6862; margin-bottom: 0.75rem; }
.block-head h3 { font-size: 0.875rem; font-weight: 600; color: #1f1f1e; }

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.75rem, 1fr));
  gap: 0.75rem;
}
.num-item {
  background: #f7f5f0;
  padding: 0.75rem 1rem;
  border-radius: 0.375rem;
}
.num-item.accent { background: #f3efff; }
.num-val { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-val.muted-num { color: #9a968d; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.125rem; }

.dual-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1rem;
}
.dual-grid > .block { margin-bottom: 0; }

.sub-h { font-size: 0.6875rem; font-weight: 600; color: #6b6862; margin: 0 0 0.5rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.status-list { list-style: none; margin: 0; padding: 0; font-size: 0.8125rem; }
.status-list li {
  display: flex;
  justify-content: space-between;
  padding: 0.375rem 0;
  border-bottom: 1px solid #f5f5f0;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}
.data-table td {
  padding: 0.375rem 0;
  border-bottom: 1px solid #f5f5f0;
}
.data-table td.r { text-align: right; font-variant-numeric: tabular-nums; }
</style>
