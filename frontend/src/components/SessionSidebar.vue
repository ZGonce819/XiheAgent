<script setup>
import { ref } from 'vue'
import ConfirmModal from './ConfirmModal.vue'

defineProps({
  threads: {
    type: Array,
    default: () => [],
  },
  activeId: {
    type: String,
    default: null,
  },
  disabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['new-thread', 'select-thread', 'delete-thread'])

const showDeleteModal = ref(false)
const pendingDeleteId = ref(null)

function titleOf(thread) {
  return thread.title || thread.repoFullName || thread.repo || '未命名任务'
}

function requestDelete(thread) {
  pendingDeleteId.value = thread.id
  showDeleteModal.value = true
}

function confirmDelete() {
  if (pendingDeleteId.value) {
    emit('delete-thread', pendingDeleteId.value)
    pendingDeleteId.value = null
  }
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-text">XiheAgent</span>
    </div>

    <button class="new-button" :disabled="disabled" @click="$emit('new-thread')">
      + 新任务
    </button>

    <div class="sidebar-section-label">任务历史</div>

    <nav class="thread-list">
      <div v-if="!threads.length" class="thread-empty">无历史记录</div>
      <button
        v-for="thread in threads"
        :key="thread.id"
        class="thread-item"
        :class="{ active: thread.id === activeId }"
        @click="$emit('select-thread', thread.id)"
      >
        <span class="status-dot" :class="thread.status"></span>
        <span class="thread-content">
          <span class="thread-title">{{ titleOf(thread) }}</span>
          <small>{{ thread.repoFullName || thread.repo }}</small>
        </span>
        <span
          class="thread-delete"
          title="删除会话"
          role="button"
          tabindex="0"
          @click.stop="requestDelete(thread)"
          @keydown.enter.stop.prevent="requestDelete(thread)"
          @keydown.space.stop.prevent="requestDelete(thread)"
        >
          ×
        </span>
      </button>
    </nav>

    <ConfirmModal
      v-model:visible="showDeleteModal"
      title="确认删除会话"
      message="该操作会同时删除会话历史、checkpoint 和业务记录，且无法恢复。"
      confirm-text="删除"
      cancel-text="取消"
      :danger="true"
      @confirm="confirmDelete"
    />
  </aside>
</template>
