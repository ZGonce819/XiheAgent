<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const PLATFORMS = [
  { id: 'github', name: 'GitHub', host: 'github.com' },
  { id: 'gitee', name: 'Gitee', host: 'gitee.com' },
  { id: 'codeup', name: 'Codeup', host: 'codeup.aliyun.com' },
]

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false,
  },
  locked: {
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
  platform: {
    type: String,
    default: 'github',
  },
})

const emit = defineEmits(['send', 'stop', 'update:repo', 'update:model', 'update:platform'])
const draft = ref('')
const showModelDropdown = ref(false)
const showPlatformDropdown = ref(false)
const textareaHeight = ref(72)
const isResizing = ref(false)
const repoInput = ref('')
const composer = ref(null)
let activeResizeCleanup = null

const MODELS = [
  { id: 'deepseek', name: 'DeepSeek' },
]

const displayModel = computed(() => {
  const modelId = props.model || 'deepseek'
  const found = MODELS.find((m) => modelId === m.id || modelId.startsWith(m.id + '-'))
  return found ? found.id : modelId
})

const displayPlatform = computed(() => {
  return PLATFORMS.find((p) => p.id === props.platform) || PLATFORMS[0]
})

function assembleRepoUrl(platformId, rawInput) {
  const trimmed = rawInput.trim().replace(/\.git$/, '').replace(/\/+$/, '')
  if (!trimmed) return ''
  if (/^https?:\/\//.test(trimmed)) return trimmed
  const platform = PLATFORMS.find((p) => p.id === platformId) || PLATFORMS[0]
  return `https://${platform.host}/${trimmed}.git`
}

function parseRepoUrl(url) {
  if (!url) return { platform: 'github', raw: '' }
  const match = url.match(/^https?:\/\/([^/]+)\/(.+?)(?:\.git)?\/?$/)
  if (match) {
    const host = match[1]
    const path = match[2]
    const platform = PLATFORMS.find((p) => p.host === host)
    if (platform) {
      return { platform: platform.id, raw: path }
    }
  }
  return { platform: props.platform, raw: url }
}

watch(
  () => props.repo,
  (newRepo) => {
    const parsed = parseRepoUrl(newRepo)
    repoInput.value = parsed.raw
    if (parsed.platform !== props.platform) {
      emit('update:platform', parsed.platform)
    }
  },
  { immediate: true },
)

watch(repoInput, (value) => {
  emit('update:repo', assembleRepoUrl(props.platform, value))
})

watch(
  () => props.platform,
  () => {
    emit('update:repo', assembleRepoUrl(props.platform, repoInput.value))
  },
)

function send() {
  const content = draft.value.trim()
  if (!content || props.disabled) return
  emit('send', content)
}

watch(
  () => props.disabled,
  (disabled, wasDisabled) => {
    if (disabled && !wasDisabled) {
      draft.value = ''
    }
  },
)

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

function selectPlatform(platformId) {
  emit('update:platform', platformId)
  showPlatformDropdown.value = false
}

function togglePlatformDropdown() {
  if (!props.disabled && !props.locked) {
    showPlatformDropdown.value = !showPlatformDropdown.value
  }
}

function onOverlayClick(event) {
  if (event.target === event.currentTarget) {
    showModelDropdown.value = false
    showPlatformDropdown.value = false
  }
}

function maxTextareaHeight() {
  if (typeof window === 'undefined') return 640
  const headerBottom = document.querySelector('.workspace-header')?.getBoundingClientRect().bottom || 0
  const composerTop = composer.value?.getBoundingClientRect().top
  const measuredLimit = composerTop == null
    ? window.innerHeight - 220
    : textareaHeight.value + composerTop - headerBottom - 8
  return Math.max(160, Math.floor(measuredLimit))
}

function clampTextareaHeight(height) {
  return Math.max(48, Math.min(maxTextareaHeight(), height))
}

function onResizeStart(event) {
  if (event.button != null && event.button !== 0) return
  event.preventDefault()
  isResizing.value = true
  const startY = event.clientY
  const startHeight = textareaHeight.value
  const pointerId = event.pointerId
  event.currentTarget?.setPointerCapture?.(pointerId)

  function onMove(e) {
    if (e.pointerId !== pointerId) return
    e.preventDefault()
    const delta = startY - e.clientY
    textareaHeight.value = clampTextareaHeight(startHeight + delta)
  }

  function onUp(e) {
    if (e.pointerId !== pointerId) return
    isResizing.value = false
    document.removeEventListener('pointermove', onMove)
    document.removeEventListener('pointerup', onUp)
    document.removeEventListener('pointercancel', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    activeResizeCleanup = null
  }

  document.body.style.cursor = 'ns-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('pointermove', onMove, { passive: false })
  document.addEventListener('pointerup', onUp)
  document.addEventListener('pointercancel', onUp)
  activeResizeCleanup = () => onUp({ pointerId })
}

function onViewportResize() {
  textareaHeight.value = clampTextareaHeight(textareaHeight.value)
}

onMounted(() => {
  window.addEventListener('resize', onViewportResize)
})

onBeforeUnmount(() => {
  activeResizeCleanup?.()
  window.removeEventListener('resize', onViewportResize)
})
</script>

<template>
  <footer ref="composer" class="composer">
    <div
      class="resize-handle"
      :class="{ active: isResizing }"
      @pointerdown="onResizeStart"
    >
      <span class="resize-grip"></span>
    </div>

    <div class="repo-field">
      <span class="repo-label">代码仓库</span>
      <div class="platform-selector">
        <button
          class="platform-badge"
          :disabled="disabled || locked"
          @click="togglePlatformDropdown"
        >
          {{ displayPlatform.name }}
          <span class="model-arrow">▾</span>
        </button>
        <Transition name="dropdown">
          <div v-if="showPlatformDropdown" class="platform-dropdown" @click="onOverlayClick">
            <div class="model-dropdown-content">
              <button
                v-for="p in PLATFORMS"
                :key="p.id"
                class="model-option"
                :class="{ active: props.platform === p.id }"
                @click="selectPlatform(p.id)"
              >
                {{ p.name }}
              </button>
            </div>
          </div>
        </Transition>
      </div>
      <div class="repo-input-wrap">
        <input
          v-model="repoInput"
          :disabled="disabled || locked"
          placeholder="owner/repo"
        />
        <button
          v-if="repoInput"
          class="repo-clear-btn"
          :disabled="disabled || locked"
          title="清空代码仓库"
          @click="repoInput = ''"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
            <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
          </svg>
        </button>
      </div>
    </div>

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
