<script setup lang="ts">
/**
 * UsersListView — 用户画像独立大 tab(INS-B Phase 4,2026-05-27 末⁵³).
 *
 * 设计宗旨(用户拍板"长远眼光,以后上万人"):
 *   1. 服务端分页(page + page_size,默认 20/页)
 *   2. 复合筛选(q / plan / risk)+ 多排序(注册 / 消费 / 风险 / 活跃)
 *   3. 邮箱 / user_id 模糊搜索 + debounce 350ms
 *   4. URL 状态同步(刷新不丢筛选)
 *   5. stale-while-revalidate(切筛选不闪)
 *   6. 高风险用户行突出(浅红底)
 *
 * 不依赖合规风控页 — 这里是独立的"用户运营"主入口.
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

interface UserRow {
  user_id: string;
  email: string;
  plan: string;
  register_ip: string | null;
  created_at: string;
  projects_count: number;
  simulations_count: number;
  violation_count: number;
  total_consumed_yuan: number;
  consumed_credits: number;
  current_credits: number;
  last_event_ms: number | null;
  is_high_risk: boolean;
}

interface UsersResp {
  total: number;
  page: number;
  page_size: number;
  huimeng_attached: boolean;
  users: UserRow[];
  filters: { q: string | null; plan: string; risk: string; sort: string };
}

const route = useRoute();
const router = useRouter();

// 从 URL 还原状态(刷新不丢)
const q = ref<string>((route.query.q as string) || "");
const plan = ref<string>((route.query.plan as string) || "all");
const risk = ref<string>((route.query.risk as string) || "all");
const sort = ref<string>((route.query.sort as string) || "created_desc");
const page = ref<number>(parseInt((route.query.page as string) || "1") || 1);
const pageSize = ref<number>(20);

const data = ref<UsersResp | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

// debounce 搜索框
let searchTimer: number | null = null;
const qInput = ref<string>(q.value);
function onSearchInput() {
  if (searchTimer !== null) clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    q.value = qInput.value;
    page.value = 1; // 搜索时跳第一页
  }, 350);
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const query: Record<string, string | number> = {
      plan: plan.value,
      risk: risk.value,
      sort: sort.value,
      page: page.value,
      page_size: pageSize.value,
    };
    if (q.value.trim()) query.q = q.value.trim();
    data.value = await fetchAdmin<UsersResp>("/admin/users", query);
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

// 同步 URL(刷新不丢状态)
function syncUrl() {
  const query: Record<string, string> = {};
  if (q.value.trim()) query.q = q.value.trim();
  if (plan.value !== "all") query.plan = plan.value;
  if (risk.value !== "all") query.risk = risk.value;
  if (sort.value !== "created_desc") query.sort = sort.value;
  if (page.value !== 1) query.page = String(page.value);
  router.replace({ query });
}

onMounted(load);
watch([q, plan, risk, sort, page], () => {
  syncUrl();
  load();
});

const totalPages = computed(() => {
  if (!data.value) return 0;
  return Math.ceil(data.value.total / data.value.page_size);
});

function gotoPage(p: number) {
  if (p < 1 || p > totalPages.value) return;
  page.value = p;
}

function fmtMs(ms: number | null): string {
  if (ms === null) return "—";
  return new Date(ms).toLocaleString("zh-CN");
}
function fmtAgo(ms: number | null): string {
  if (ms === null) return "—";
  const diff = Date.now() - ms;
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分前`;
  if (diff < 86400_000) return `${Math.floor(diff / 3600_000)} 小时前`;
  return `${Math.floor(diff / 86400_000)} 天前`;
}

const PLAN_LABELS: Record<string, string> = {
  free: "Free",
  pro: "Pro",
  max: "Max",
  super_max: "Super",
  founder: "创始人",
};
function planLabel(p: string): string {
  return PLAN_LABELS[p] || p;
}

function resetFilters() {
  qInput.value = "";
  q.value = "";
  plan.value = "all";
  risk.value = "all";
  sort.value = "created_desc";
  page.value = 1;
}
</script>

<template>
  <section>
    <PageHero
      icon="users"
      title="用户画像"
      description="平台所有用户的全景列表 — 邮箱搜索 / 订阅档筛选 / 风险筛选 / 多维度排序 / 服务端分页。点用户名进入完整画像(账户/余额/作品/上传/红旗/评分)。"
      audience="用户运营视角"
    />

    <!-- 搜索 + 筛选栏 -->
    <div class="filter-bar">
      <div class="search-box">
        <Icon name="search" :size="14" class="search-icon" />
        <input
          v-model="qInput"
          type="text"
          placeholder="搜索邮箱 / user_id (350ms 自动)"
          class="search-input"
          @input="onSearchInput"
        />
      </div>

      <div class="filter">
        <label>订阅档</label>
        <select v-model="plan">
          <option value="all">全部</option>
          <option value="free">Free</option>
          <option value="pro">Pro</option>
          <option value="max">Max</option>
          <option value="super_max">SuperMax</option>
          <option value="founder">创始人</option>
        </select>
      </div>

      <div class="filter">
        <label>风险</label>
        <select v-model="risk">
          <option value="all">全部</option>
          <option value="has_violation">有红旗命中</option>
          <option value="high_risk">高风险(≥ 3 次)</option>
        </select>
      </div>

      <div class="filter">
        <label>排序</label>
        <select v-model="sort">
          <option value="created_desc">注册最新</option>
          <option value="created_asc">注册最早</option>
          <option value="consumed_desc">消费最多</option>
          <option value="violation_desc">命中最多</option>
          <option value="last_active_desc">最近活跃</option>
        </select>
      </div>

      <button class="reset-btn" @click="resetFilters">
        <Icon name="refresh" :size="14" /> 重置
      </button>
    </div>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接
    </div>

    <!-- 结果 meta -->
    <div v-if="data" class="meta-row">
      <span class="meta-text">
        共 <b>{{ data.total }}</b> 个用户
        <span v-if="totalPages > 0">· 第 {{ data.page }} / {{ totalPages }} 页</span>
        <span v-if="loading" class="loading-tag">加载中…</span>
      </span>
    </div>

    <!-- 用户表 -->
    <div v-if="data && data.users.length" class="table-wrap">
      <table class="users-table">
        <thead>
          <tr>
            <th>邮箱</th>
            <th>订阅档</th>
            <th class="r">项目 · 推演</th>
            <th class="r">累计消费</th>
            <th class="r">额度 耗 / 余</th>
            <th class="r">红旗</th>
            <th>最近活跃</th>
            <th>注册时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="u in data.users"
            :key="u.user_id"
            :class="{ 'high-risk': u.is_high_risk }"
          >
            <td>
              <div class="email">{{ u.email }}</div>
              <code class="uid">{{ u.user_id.slice(0, 12) }}…</code>
            </td>
            <td>
              <span class="plan-chip" :class="`plan-${u.plan}`">
                {{ planLabel(u.plan) }}
              </span>
            </td>
            <td class="r">
              <b>{{ u.projects_count }}</b><span class="muted"> · </span><b>{{ u.simulations_count }}</b>
            </td>
            <td class="r">
              <b>¥{{ u.total_consumed_yuan.toFixed(2) }}</b>
            </td>
            <td class="r">
              <b>{{ u.consumed_credits }}</b><span class="muted"> / </span><span class="muted-small">{{ u.current_credits }}</span>
            </td>
            <td class="r">
              <span v-if="u.violation_count > 0" class="violation-num" :class="{ critical: u.is_high_risk }">
                {{ u.violation_count }}
              </span>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <div>{{ fmtAgo(u.last_event_ms) }}</div>
              <div class="muted-small">{{ fmtMs(u.last_event_ms) }}</div>
            </td>
            <td class="muted-small">{{ u.created_at }}</td>
            <td>
              <router-link :to="`/user/${u.user_id}`" class="link-btn">画像</router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else-if="data && !loading" class="empty">
      没有匹配的用户。试试清空筛选条件,或者主平台还没有用户注册。
    </div>

    <!-- 分页 -->
    <div v-if="data && totalPages > 1" class="pagination">
      <button
        class="page-btn"
        :disabled="page <= 1"
        @click="gotoPage(page - 1)"
      >
        <Icon name="chevron-left" :size="14" /> 上一页
      </button>
      <span class="page-info">第 {{ page }} / {{ totalPages }} 页</span>
      <button
        class="page-btn"
        :disabled="page >= totalPages"
        @click="gotoPage(page + 1)"
      >
        下一页 <Icon name="chevron-right" :size="14" />
      </button>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.warn { color: #92400e; padding: 0.75rem; background: #fef3c7; border-radius: 0.375rem; margin-bottom: 1rem; font-size: 0.8125rem; }
.empty {
  color: #9a968d;
  font-style: italic;
  padding: 3rem 1.5rem;
  text-align: center;
  font-size: 0.8125rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
}

/* 筛选栏 */
.filter-bar {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  background: white;
  padding: 0.875rem 1.125rem;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
}
.search-box {
  flex: 1;
  min-width: 15rem;
  position: relative;
}
.search-icon {
  position: absolute;
  left: 0.625rem;
  top: 50%;
  transform: translateY(-50%);
  color: #9a968d;
}
.search-input {
  width: 100%;
  padding: 0.5rem 0.75rem 0.5rem 2rem;
  font-size: 0.8125rem;
  border: 1px solid #d6d1c4;
  border-radius: 0.375rem;
  background: white;
}
.search-input:focus {
  outline: none;
  border-color: #c4b5fd;
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.1);
}

.filter { display: flex; flex-direction: column; gap: 0.25rem; }
.filter label { font-size: 0.6875rem; color: #6b6862; }
.filter select {
  font-size: 0.8125rem;
  padding: 0.4375rem 0.625rem;
  border: 1px solid #d6d1c4;
  border-radius: 0.25rem;
  background: white;
  min-width: 8.125rem;
}
.reset-btn {
  font-size: 0.75rem;
  padding: 0.4375rem 0.875rem;
  border: 1px solid #d6d1c4;
  background: white;
  color: #6b6862;
  border-radius: 0.25rem;
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}
.reset-btn:hover { background: #f7f5f0; }

/* meta */
.meta-row {
  font-size: 0.75rem;
  color: #6b6862;
  margin-bottom: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.meta-text b { color: #1f1f1e; font-variant-numeric: tabular-nums; }
.loading-tag {
  background: #f3efff;
  color: #7c3aed;
  padding: 0.125rem 0.625rem;
  border-radius: 62.4375rem;
  font-size: 0.6875rem;
  font-weight: 500;
}

/* 表 */
.table-wrap {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  overflow: hidden;
}
.users-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}
.users-table thead { background: #f7f5f0; }
.users-table th {
  padding: 0.625rem 0.875rem;
  text-align: left;
  font-weight: 600;
  color: #6b6862;
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03125rem;
}
.users-table td {
  padding: 0.75rem 0.875rem;
  border-top: 1px solid #f0ece3;
}
.users-table td.r, .users-table th.r {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.users-table tr.high-risk {
  background: linear-gradient(90deg, rgba(254, 226, 226, 0.3) 0%, transparent 30%);
}

.email { font-weight: 500; }
.uid {
  font-size: 0.6875rem;
  color: #9a968d;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  display: block;
  margin-top: 0.125rem;
}
.muted { color: #9a968d; }
.muted-small { color: #9a968d; font-size: 0.6875rem; }

.plan-chip {
  font-size: 0.6875rem;
  padding: 0.125rem 0.625rem;
  border-radius: 0.1875rem;
  font-weight: 600;
  background: #ede9e0;
  color: #6b6862;
}
.plan-chip.plan-pro { background: #f3efff; color: #5b21b6; }
.plan-chip.plan-max { background: #dbeafe; color: #1e40af; }
.plan-chip.plan-super_max { background: #d1fae5; color: #065f46; }
.plan-chip.plan-founder { background: #fef3c7; color: #d97706; }

.violation-num {
  background: #fef3c7;
  color: #d97706;
  padding: 0.125rem 0.5rem;
  border-radius: 0.1875rem;
  font-weight: 600;
}
.violation-num.critical {
  background: #fee2e2;
  color: #dc2626;
}

.link-btn {
  font-size: 0.6875rem;
  color: #7c3aed;
  text-decoration: none;
  padding: 0.25rem 0.75rem;
  border: 1px solid #c4b5fd;
  border-radius: 0.25rem;
}
.link-btn:hover { background: #f3efff; }

/* 分页 */
.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 1rem;
  margin-top: 1.25rem;
}
.page-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  padding: 0.375rem 0.875rem;
  border: 1px solid #d6d1c4;
  background: white;
  border-radius: 0.25rem;
  color: #1f1f1e;
}
.page-btn:hover:not(:disabled) { background: #f7f5f0; }
.page-btn:disabled { opacity: 0.4; }
.page-info {
  font-size: 0.75rem;
  color: #6b6862;
  font-variant-numeric: tabular-nums;
}
</style>
