<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false,
  },
  model: {
    type: String,
    default: '',
  },
  effort: {
    type: String,
    default: 'default',
  },
  repo: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['send', 'stop', 'update:repo', 'update:model'])
const draft = ref('')
const showModelDropdown = ref(false)
const textareaHeight = ref(72)
const isResizing = ref(false)

const MODELS = [
  { id: 'deepseek', name: 'DeepSeek' },
]

const displayModel = computed(() => {
  const modelId = props.model || 'deepseek'
  const found = MODELS.find((m) => modelId === m.id || modelId.startsWith(m.id + '-'))
  return found ? found.id : modelId
})

function send() {
  const content = draft.value.trim()
  if (!content || props.disabled) return
  draft.value = ''
  emit('send', content)
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function selectModel(modelId) {
  emit('update:model', modelId)
  showModelDropdown.value = false
}

function toggleModelDropdown() {
  if (!props.disabled) {
    showModelDropdown.value = !showModelDropdown.value
  }
}

function onOverlayClick(event) {
  if (event.target === event.currentTarget) {
    showModelDropdown.value = false
  }
}

function onResizeStart(event) {
  event.preventDefault()
  isResizing.value = true
  const startY = event.clientY
  const startHeight = textareaHeight.value

  function onMove(e) {
    const delta = startY - e.clientY
    textareaHeight.value = Math.max(48, Math.min(400, startHeight + delta))
  }

  function onUp() {
    isResizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.body.style.cursor = 'ns-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

onBeforeUnmount(() => {
  isResizing.value = false
})
</script>

<template>
  <footer class="composer">
    <div
      class="resize-handle"
      :class="{ active: isResizing }"
      @mousedown="onResizeStart"
    >
      <span class="resize-grip"></span>
    </div>

    <label class="repo-field">
      <span>代码仓库</span>
      <input
        :value="repo"
        :disabled="disabled"
        placeholder="owner/repo"
        @input="$emit('update:repo', $event.target.value)"
      />
    </label>

    <textarea
      v-model="draft"
      :disabled="disabled"
      :style="{ height: textareaHeight + 'px' }"
      placeholder="继续输入指令"
      @keydown="onKeydown"
    />

    <div class="composer-footer">
      <div class="model-selector">
        <button
          class="model-badge"
          :disabled="disabled"
          @click="toggleModelDropdown"
        >
          {{ displayModel }}
          <span class="model-arrow">▾</span>
        </button>
        <Transition name="dropdown">
          <div v-if="showModelDropdown" class="model-dropdown" @click="onOverlayClick">
            <div class="model-dropdown-content">
              <button
                v-for="m in MODELS"
                :key="m.id"
                class="model-option"
                :class="{ active: displayModel === m.id }"
                @click="selectModel(m.id)"
              >
                {{ m.name }}
              </button>
            </div>
          </div>
        </Transition>
      </div>
      <span class="shortcut-hint">Enter 发送 · Shift+Enter 换行</span>
      <button v-if="disabled" class="stop-button" @click="$emit('stop')">停止</button>
      <button v-else class="send-button" :disabled="!draft.trim()" @click="send">发送</button>
    </div>
  </footer>
</template>
