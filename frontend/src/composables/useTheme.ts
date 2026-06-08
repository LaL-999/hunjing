/**
 * useTheme — 主题切换(Sprint D.4)。
 *
 * 三态选择:
 *   "light"   永远亮色(忽略系统)
 *   "dark"    永远暗色(忽略系统)
 *   "system"  跟随系统 prefers-color-scheme(默认)
 *
 * 设计要点:
 *   ① **DOM 应用**:`<html data-theme="dark">` 触发 tokens.css 暗色 token 覆盖
 *      effective theme:system → 读 prefers-color-scheme;light/dark → 直接用
 *   ② **localStorage 持久化** key="huimeng:theme:v1",刷新后保留选择
 *   ③ **FOUC 防御**:main.ts 在 createApp 前调用 applyInitialTheme(),
 *      DOM `<html>` 在 Vue mount 前就已挂正确 data-theme,**绝无白闪一帧**
 *   ④ **跟随系统模式实时响应**:用 matchMedia listener 监听系统切换
 *      (用户系统主题变 → 浑晶页面自动切)
 *
 * 用法:
 *   const theme = useTheme();
 *   theme.preference.value   // "light" | "dark" | "system" — 用户选择
 *   theme.effective.value    // "light" | "dark" — 实际生效
 *   theme.setPreference("dark")   // 切换 + 持久化 + 应用 DOM
 */
import { ref, computed, onMounted, onBeforeUnmount } from "vue";

export type ThemePreference = "light" | "dark" | "system";
export type EffectiveTheme = "light" | "dark";

const STORAGE_KEY = "huimeng:theme:v1";

/** 模块级单例 state — 整个 app 共享主题(不像普通 composable 每次新建)*/
const preference = ref<ThemePreference>("system");
const systemPrefersDark = ref(false);
let mediaQuery: MediaQueryList | null = null;
let mqListener: ((e: MediaQueryListEvent) => void) | null = null;

/** 实际生效的主题 — preference=system 时跟随系统 */
const effective = computed<EffectiveTheme>(() => {
  if (preference.value === "system") {
    return systemPrefersDark.value ? "dark" : "light";
  }
  return preference.value;
});

function readStoragePreference(): ThemePreference {
  if (typeof window === "undefined" || !window.localStorage) return "system";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
    return "system";
  } catch {
    return "system";
  }
}

function writeStoragePreference(pref: ThemePreference) {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, pref);
  } catch {
    /* 隐私模式 / quota 满 — 不阻塞切换 */
  }
}

function readSystemPref(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyToDom(theme: EffectiveTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "dark") {
    root.setAttribute("data-theme", "dark");
  } else {
    root.removeAttribute("data-theme");
  }
}

/** main.ts 调一次,DOM 挂正确 data-theme + 启动系统主题监听。
 *  必须在 createApp 之前 — 否则 Vue mount 时第一帧用错颜色。
 */
export function applyInitialTheme(): void {
  preference.value = readStoragePreference();
  systemPrefersDark.value = readSystemPref();
  applyToDom(effective.value);

  // 系统主题切换实时响应(preference=system 时才有效)
  if (typeof window !== "undefined" && window.matchMedia) {
    mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    mqListener = (e: MediaQueryListEvent) => {
      systemPrefersDark.value = e.matches;
      if (preference.value === "system") {
        applyToDom(effective.value);
      }
    };
    mediaQuery.addEventListener("change", mqListener);
  }
}

export function useTheme() {
  // 组件 mount 时刷一次系统 pref(防 SSR / 测试场景下 state 漂移)
  onMounted(() => {
    systemPrefersDark.value = readSystemPref();
  });

  onBeforeUnmount(() => {
    // 不在这里 detach mqListener — 它是 app 级单例,直到 app 销毁才需清
  });

  function setPreference(pref: ThemePreference) {
    preference.value = pref;
    writeStoragePreference(pref);
    applyToDom(effective.value);
  }

  return {
    preference,
    effective,
    setPreference,
  };
}
