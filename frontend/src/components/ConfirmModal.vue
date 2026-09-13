<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false,
  },
  title: {
    type: String,
    default: '确认操作',
  },
  message: {
    type: String,
    default: '',
  },
  confirmText: {
    type: String,
    default: '确定',
  },
  cancelText: {
    type: String,
    default: '取消',
  },
  danger: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['confirm', 'cancel', 'update:visible'])

function onConfirm() {
  emit('confirm')
  emit('update:visible', false)
}

function onCancel() {
  emit('cancel')
  emit('update:visible', false)
}

function onOverlayClick(event) {
  if (event.target === event.currentTarget) {
    onCancel()
  }
}

watch(() => props.visible, (val) => {
  if (val) {
    document.body.style.overflow = 'hidden'
  } else {
    document.body.style.overflow = ''
  }
})
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="visible" class="modal-overlay" @click="onOverlayClick">
        <div class="modal-content" role="dialog" aria-modal="true">
          <div class="modal-header">
            <h3>{{ title }}</h3>
          </div>
          <div class="modal-body">
            <p>{{ message }}</p>
          </div>
          <div class="modal-footer">
            <button class="modal-btn modal-btn-cancel" @click="onCancel">
              {{ cancelText }}
            </button>
            <button
              class="modal-btn"
              :class="danger ? 'modal-btn-danger' : 'modal-btn-primary'"
              @click="onConfirm"
            >
              {{ confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
}

.modal-content {
  width: 100%;
  max-width: 420px;
  margin: 0 var(--space-4);
  border-radius: var(--radius-xl);
  background: var(--color-bg-tertiary);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  animation: modalIn 200ms cubic-bezier(0.4, 0, 0.2, 1);
}

@keyframes modalIn {
  from {
    opacity: 0;
    transform: scale(0.95) translateY(10px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

.modal-header {
  padding: var(--space-5) var(--space-5) 0;
}

.modal-header h3 {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.modal-body {
  padding: var(--space-3) var(--space-5);
}

.modal-body p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
}

.modal-btn {
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-weight: 500;
  font-size: var(--text-sm);
  padding: var(--space-2) var(--space-4);
  transition: all var(--transition-fast);
}

.modal-btn-cancel {
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  border: 1px solid var(--color-border-primary);
}

.modal-btn-cancel:hover {
  background: var(--color-bg-hover);
  color: var(--color-text-primary);
}

.modal-btn-primary {
  background: var(--color-accent);
  color: var(--color-text-inverse);
}

.modal-btn-primary:hover {
  background: var(--color-accent-hover);
}

.modal-btn-danger {
  background: var(--color-danger);
  color: var(--color-text-inverse);
}

.modal-btn-danger:hover {
  background: #dc2626;
}

.modal-enter-active,
.modal-leave-active {
  transition: opacity 150ms ease;
}

.modal-enter-active .modal-content,
.modal-leave-active .modal-content {
  transition: transform 150ms ease, opacity 150ms ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-content,
.modal-leave-to .modal-content {
  transform: scale(0.95) translateY(10px);
  opacity: 0;
}
</style>
