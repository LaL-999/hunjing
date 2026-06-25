<script setup lang="ts">
/**
 * EditProfileModal — 编辑个人资料(昵称 + 头像)。2026-06-25。
 *
 * 配合作品广场:作者展示用昵称 + 头像,不再只显示邮箱首字母。
 * 保存后回写 auth.currentUser(nickname / avatar_url),侧栏立即刷新。
 *
 * 黄金标准参照:CreateComicModal.vue 的 backdrop + blur + Esc + Teleport body。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { apiAssetUrl, ApiError } from "../api/client";
import { profileApi } from "../api/plaza";
import { useAuthStore } from "../stores/auth";
import { toast } from "../composables/useToast";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();

const auth = useAuthStore();

const nickname = ref("");
const avatarUrl = ref<string | null>(null);
const savingNick = ref(false);
const uploadingAvatar = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

const email = computed(() => auth.currentUser?.email ?? "");
const avatarInitial = computed(() => {
  const n = nickname.value.trim() || email.value;
  return n[0]?.toUpperCase() ?? "?";
});

const canSaveNick = computed(
  () =>
    nickname.value.trim().length > 0
    && nickname.value.trim().length <= 24
    && nickname.value.trim() !== (auth.currentUser?.nickname ?? "")
    && !savingNick.value,
);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      nickname.value = auth.currentUser?.nickname ?? "";
      avatarUrl.value = auth.currentUser?.avatar_url ?? null;
    }
  },
);

function triggerUpload(): void {
  fileInput.value?.click();
}

async function onFilePicked(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    toast.error("头像图最大 5 MB");
    return;
  }
  uploadingAvatar.value = true;
  try {
    const profile = await profileApi.uploadAvatar(file);
    avatarUrl.value = profile.avatar_url;
    if (auth.currentUser) auth.currentUser.avatar_url = profile.avatar_url;
    toast.success("头像已更新");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "头像上传失败");
  } finally {
    uploadingAvatar.value = false;
  }
}

async function saveNickname(): Promise<void> {
  if (!canSaveNick.value) return;
  savingNick.value = true;
  try {
    const profile = await profileApi.updateNickname(nickname.value.trim());
    if (auth.currentUser) auth.currentUser.nickname = profile.nickname;
    toast.success("昵称已更新");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存昵称失败");
  } finally {
    savingNick.value = false;
  }
}

function handleBackdrop(e: MouseEvent): void {
  if (e.target === e.currentTarget) emit("close");
}
function onGlobalKey(e: KeyboardEvent): void {
  if (!props.open) return;
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}
onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-title"
        @click="handleBackdrop"
      >
        <div class="modal-card surface" role="document">
          <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">×</button>

          <header class="modal-header">
            <h2 id="profile-title" class="modal-title">编辑资料</h2>
            <p class="modal-subtitle">你的昵称和头像会展示在作品广场上</p>
          </header>

          <!-- 头像 -->
          <div class="avatar-row">
            <span class="avatar-big">
              <img v-if="avatarUrl" :src="apiAssetUrl(avatarUrl)" alt="头像" />
              <template v-else>{{ avatarInitial }}</template>
            </span>
            <div class="avatar-actions">
              <button
                type="button"
                class="upload-btn"
                :disabled="uploadingAvatar"
                @click="triggerUpload"
              >
                {{ uploadingAvatar ? "上传中…" : (avatarUrl ? "更换头像" : "上传头像") }}
              </button>
              <p class="field-sub">JPG / PNG / WebP,≤5MB</p>
            </div>
            <input ref="fileInput" type="file" accept="image/*" class="hidden-file" @change="onFilePicked" />
          </div>

          <!-- 昵称 -->
          <div class="field">
            <label class="field-label" for="nick">昵称</label>
            <div class="nick-row">
              <input
                id="nick"
                v-model="nickname"
                type="text"
                maxlength="24"
                placeholder="给自己取个昵称"
                class="text-input"
                autocomplete="off"
                @keydown.enter.prevent="saveNickname"
              />
              <button
                type="button"
                class="btn btn-primary"
                :disabled="!canSaveNick"
                @click="saveNickname"
              >{{ savingNick ? "保存中…" : "保存" }}</button>
            </div>
            <div class="field-hint mono">{{ nickname.trim().length }}/24</div>
          </div>

          <!-- 邮箱(只读) -->
          <div class="field">
            <label class="field-label">登录邮箱</label>
            <div class="readonly-box mono">{{ email || "—" }}</div>
          </div>

          <footer class="modal-footer">
            <button type="button" class="btn btn-ghost" @click="emit('close')">完成</button>
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
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}
.modal-card {
  width: 100%;
  max-width: 460px;
  padding: var(--space-6) var(--space-6) var(--space-5);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover { background: var(--color-surface-hover); color: var(--color-text); }

.modal-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title { font-size: var(--text-xl); font-weight: 600; color: var(--color-text); margin: 0; }
.modal-subtitle { font-size: var(--text-sm); color: var(--color-text-muted); margin: 0; }

.avatar-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.avatar-big {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #7C3AED, #22D3A8);
  overflow: hidden;
}
.avatar-big img { width: 100%; height: 100%; object-fit: cover; }
.avatar-actions { display: flex; flex-direction: column; gap: var(--space-1); }
.upload-btn {
  align-self: flex-start;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.upload-btn:hover:not(:disabled) { border-color: var(--color-accent-border); background: var(--color-surface-hover); }
.upload-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.hidden-file { display: none; }
.field-sub { font-size: var(--text-xs); color: var(--color-text-muted); margin: 0; }

.field { display: flex; flex-direction: column; gap: var(--space-2); }
.field-label { font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
.field-hint { display: flex; justify-content: flex-end; font-size: var(--text-xs); color: var(--color-text-subtle); }
.nick-row { display: flex; gap: var(--space-2); }
.text-input {
  flex: 1;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out), box-shadow var(--duration-fast) var(--ease-out);
}
.text-input:focus { border-color: var(--color-accent); box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12); }
.readonly-box {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.btn { padding: var(--space-2) var(--space-5); font-size: var(--text-sm); font-weight: 500; border-radius: var(--radius-md); cursor: pointer; transition: all var(--duration-fast) var(--ease-out); }
.btn-ghost { color: var(--color-text); background: transparent; border: 1px solid var(--color-border); }
.btn-ghost:hover { background: var(--color-surface-hover); border-color: var(--color-border-strong); }
.btn-primary { color: var(--color-text-on-accent); background: var(--color-accent); border: 1px solid var(--color-accent); }
.btn-primary:hover:not(:disabled) { background: var(--color-accent-hover); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.mono { font-family: var(--font-mono); }
</style>
