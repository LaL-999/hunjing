<script setup lang="ts">
/**
 * RetentionView — 留存率(INS-A9 整改,2026-05-27 末³).
 *
 * PageHero 承担说明.留存率格子加色阶视觉(留存高=深紫,低=浅).
 */
import { onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";

interface RetentionResp {
  cohort_day: number;
  cohort_size: number;
  retention: { day: number; active: number; rate: number }[];
}

const data = ref<RetentionResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<RetentionResp>("/admin/retention", { days: 7 });
  } catch (e) {
    error.value = String(e);
  }
}

// 把 rate(0~1)映射到浅紫 → 深紫色阶,作为留存高低的视觉编码.
function rateBg(rate: number): string {
  const clamped = Math.max(0, Math.min(1, rate));
  const alpha = 0.08 + clamped * 0.42; // 0.08 ~ 0.5
  return `rgba(124, 58, 237, ${alpha.toFixed(3)})`;
}

onMounted(load);
</script>

<template>
  <section>
    <PageHero
      icon="line-chart"
      title="留存(7 日 cohort)"
      description="新用户首日之后 7 天内每天还回来用平台的比例,衡量产品粘性。Day 1 跌幅大 = 新用户体验问题;Day 7 还高 = 真用户。"
      :audience="data ? `Cohort 大小:${data.cohort_size} 人` : '管理员视角'"
    />

    <div v-if="error" class="err">{{ error }}</div>

    <table v-if="data && data.retention.length" class="r-table">
      <thead>
        <tr>
          <th>Day</th>
          <th class="r">活跃数</th>
          <th class="r">回访率</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in data.retention" :key="row.day" :style="{ background: rateBg(row.rate) }">
          <td>Day {{ row.day }}</td>
          <td class="r">{{ row.active }}</td>
          <td class="r">{{ (row.rate * 100).toFixed(1) }}%</td>
        </tr>
      </tbody>
    </table>

    <p v-else-if="data" class="empty">cohort 为空(暂无数据)</p>
  </section>
</template>

<style scoped>
.r-table {
  border-collapse: collapse;
  width: 100%;
  max-width: 30rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  overflow: hidden;
}
.r-table th, .r-table td {
  padding: 0.625rem 1rem;
  font-size: 0.8125rem;
}
.r-table thead { background: #f7f5f0; }
.r-table th { font-weight: 600; color: #6b6862; text-align: left; }
.r-table tbody tr:not(:last-child) { border-bottom: 0.0625rem solid #f0ece3; }
.r-table td.r, .r-table th.r {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.r-table tbody td:last-child { font-weight: 600; }

.empty {
  margin-top: 0.75rem;
  padding: 1.5rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  text-align: center;
  font-size: 0.8125rem;
  color: #9a968d;
}

.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; }
</style>
