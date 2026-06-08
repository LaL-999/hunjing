<script setup lang="ts">
/**
 * SafetyView — B3 合规风控(INS-B Phase 1 + Phase 4 sub-tab 改造,2026-05-27 末⁵³).
 *
 * 数据源:huimeng.violation_logs / red_flag_dictionary / users(register_ip)
 *
 * Phase 4 改造:4 块内容拆 sub-tab 渲染,大数据量友好 + 信息密度降低.
 * 4 卡顶部统计保留(始终可见),4 块明细按 tab 切.
 */
import { computed, onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import TabBar from "../components/TabBar.vue";

interface SafetyResp {
  huimeng_attached: boolean;
  violations_total: number;
  violations_this_month: number;
  by_category: { category: string; count: number }[];
  top_patterns: {
    pattern: string;
    category: string;
    severity: string;
    hit_count: number;
  }[];
  high_risk_users: {
    user_id: string;
    email: string;
    violation_count: number;
  }[];
  same_ip_accounts: {
    ip: string;
    account_count: number;
    accounts: { user_id: string; email: string }[];
  }[];
}

const data = ref<SafetyResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<SafetyResp>("/admin/safety");
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

// Phase 4:sub-tab
const activeTab = ref<"category" | "patterns" | "users" | "ips">("category");

const tabs = computed(() => {
  if (!data.value) {
    return [
      { key: "category", label: "按类别", icon: "alert-triangle" },
      { key: "patterns", label: "命中词", icon: "search" },
      { key: "users", label: "高风险用户", icon: "user" },
      { key: "ips", label: "同 IP 多账号", icon: "users" },
    ];
  }
  return [
    {
      key: "category",
      label: "按类别",
      icon: "alert-triangle",
      badge: data.value.by_category.length,
    },
    {
      key: "patterns",
      label: "命中词",
      icon: "search",
      badge: data.value.top_patterns.length,
    },
    {
      key: "users",
      label: "高风险用户",
      icon: "user",
      badge: data.value.high_risk_users.length,
    },
    {
      key: "ips",
      label: "同 IP 多账号",
      icon: "users",
      badge: data.value.same_ip_accounts.length,
    },
  ];
});

const CAT_LABELS: Record<string, string> = {
  political: "政治",
  sexual: "性",
  violence: "暴力",
  privacy: "隐私",
};
const CAT_COLORS: Record<string, string> = {
  political: "#dc2626",
  sexual: "#d97706",
  violence: "#b91c1c",
  privacy: "#5b21b6",
};
function catLabel(c: string): string { return CAT_LABELS[c] || c; }
function catColor(c: string): string { return CAT_COLORS[c] || "#6b6862"; }
</script>

<template>
  <section>
    <PageHero
      icon="shield"
      title="合规风控"
      description="红旗词典命中分析 — 看清什么用户在违规、踩了哪些红线、是否有同 IP 多账号刷量。命中 ≥ 3 次的用户需关注;同 IP 多账号需查刷号嫌疑。"
      audience="法务 / 风控视角"
    />

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接,无法读 violation_logs
    </div>

    <div v-if="data && data.huimeng_attached">
      <!-- 顶部统计卡(始终可见) -->
      <div class="num-grid">
        <div class="num-item">
          <div class="num-val">{{ data.violations_total }}</div>
          <div class="num-label">总命中次数(累计)</div>
        </div>
        <div class="num-item">
          <div class="num-val crit">{{ data.violations_this_month }}</div>
          <div class="num-label">本月新增命中</div>
        </div>
        <div class="num-item">
          <div class="num-val">{{ data.high_risk_users.length }}</div>
          <div class="num-label">高风险用户(≥ 3 次)</div>
        </div>
        <div class="num-item">
          <div class="num-val">{{ data.same_ip_accounts.length }}</div>
          <div class="num-label">同 IP 多账号 IP 数</div>
        </div>
      </div>

      <!-- sub-tab 切换 -->
      <TabBar v-model="activeTab" :tabs="tabs" />

      <!-- Tab 内容 -->
      <!-- 1. 按类别 -->
      <div v-if="activeTab === 'category'" class="tab-content">
        <div v-if="data.by_category.length" class="cat-grid">
          <div
            v-for="c in data.by_category"
            :key="c.category"
            class="cat-card"
            :style="{ borderColor: catColor(c.category) }"
          >
            <div class="cat-name">{{ catLabel(c.category) }}</div>
            <div class="cat-count" :style="{ color: catColor(c.category) }">{{ c.count }}</div>
          </div>
        </div>
        <div v-else class="empty">无类别命中</div>
      </div>

      <!-- 2. top 20 命中词 -->
      <div v-if="activeTab === 'patterns'" class="tab-content">
        <table v-if="data.top_patterns.length" class="data-table">
          <thead>
            <tr>
              <th>命中词</th>
              <th>类别</th>
              <th>等级</th>
              <th class="r">次数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.top_patterns" :key="p.pattern">
              <td><code>{{ p.pattern }}</code></td>
              <td>
                <span class="cat-chip" :style="{ background: catColor(p.category), color: 'white' }">
                  {{ catLabel(p.category) }}
                </span>
              </td>
              <td>
                <span class="sev-chip" :class="`sev-${p.severity}`">
                  {{ p.severity === "block" ? "拦截" : "警告" }}
                </span>
              </td>
              <td class="r"><b>{{ p.hit_count }}</b></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">无命中数据</div>
      </div>

      <!-- 3. 高风险用户 -->
      <div v-if="activeTab === 'users'" class="tab-content">
        <table v-if="data.high_risk_users.length" class="data-table">
          <thead>
            <tr>
              <th>邮箱</th>
              <th>user_id</th>
              <th class="r">命中次数</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in data.high_risk_users" :key="u.user_id">
              <td>{{ u.email }}</td>
              <td class="muted"><code>{{ u.user_id.slice(0, 12) }}…</code></td>
              <td class="r crit-num"><b>{{ u.violation_count }}</b></td>
              <td>
                <router-link :to="`/user/${u.user_id}`" class="link-btn">查看画像</router-link>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无高风险用户</div>
      </div>

      <!-- 4. 同 IP 多账号 -->
      <div v-if="activeTab === 'ips'" class="tab-content">
        <div v-if="data.same_ip_accounts.length">
          <div
            v-for="g in data.same_ip_accounts"
            :key="g.ip"
            class="ip-group"
          >
            <div class="ip-head">
              <span class="ip-addr">{{ g.ip }}</span>
              <span class="ip-count">{{ g.account_count }} 个账号</span>
            </div>
            <ul class="ip-accounts">
              <li v-for="a in g.accounts" :key="a.user_id">
                {{ a.email }}
                <router-link :to="`/user/${a.user_id}`" class="link-btn small">画像</router-link>
              </li>
            </ul>
          </div>
        </div>
        <div v-else class="empty">未发现同 IP 多账号</div>
      </div>
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

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.75rem, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}
.num-item {
  background: white;
  border: 1px solid #e5e1d8;
  padding: 0.875rem 1rem;
  border-radius: 0.5rem;
}
.num-val { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-val.crit { color: #dc2626; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.125rem; }

.tab-content {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
}

.cat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.75rem;
}
.cat-card {
  padding: 0.875rem 1rem;
  border: 1px solid;
  border-radius: 0.375rem;
  background: white;
}
.cat-name { font-size: 0.75rem; color: #6b6862; font-weight: 600; }
.cat-count { font-size: 1.375rem; font-weight: 700; margin-top: 0.25rem; font-variant-numeric: tabular-nums; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}
.data-table th, .data-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #f5f5f0;
  text-align: left;
}
.data-table th { font-weight: 600; color: #6b6862; font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.data-table td.r, .data-table th.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table code {
  background: #f5f5f0;
  padding: 0.125rem 0.375rem;
  border-radius: 0.1875rem;
  font-size: 0.75rem;
}
.muted { color: #9a968d; }
.crit-num { color: #dc2626; }

.cat-chip, .sev-chip {
  font-size: 0.6875rem;
  padding: 0.125rem 0.5rem;
  border-radius: 0.1875rem;
}
.sev-block { background: #fee2e2; color: #dc2626; }
.sev-warn { background: #fef3c7; color: #d97706; }

.link-btn {
  font-size: 0.6875rem;
  color: #7c3aed;
  text-decoration: none;
  padding: 0.125rem 0.5rem;
  border: 1px solid #c4b5fd;
  border-radius: 0.1875rem;
}
.link-btn:hover { background: #f3efff; }
.link-btn.small { font-size: 0.625rem; padding: 0.0625rem 0.375rem; margin-left: 0.5rem; }

.ip-group {
  border-top: 1px solid #f5f5f0;
  padding: 0.625rem 0;
}
.ip-group:first-child { border-top: 0; padding-top: 0; }
.ip-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.8125rem;
}
.ip-addr { font-weight: 600; font-variant-numeric: tabular-nums; }
.ip-count { color: #dc2626; font-size: 0.75rem; }
.ip-accounts { list-style: none; margin: 0.375rem 0 0 0; padding: 0 0 0 1rem; font-size: 0.75rem; }
.ip-accounts li { padding: 0.1875rem 0; color: #6b6862; }
</style>
