<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

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

function selectPlatform(platformId) {
  emit('update:platform', platformId)
  showPlatformDropdown.value = false
}

function togglePlatformDropdown() {
  if (!props.disabled) {
    showPlatformDropdown.value = !showPlatformDropdown.value
  }
}

function onOverlayClick(event) {
  if (event.target === event.currentTarget) {
    showModelDropdown.value = false
    showPlatformDropdown.value = false
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

    <div class="repo-field">
      <span class="repo-label">代码仓库</span>
      <div class="platform-selector">
        <button
          class="platform-badge"
          :disabled="disabled"
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
      <input
        v-model="repoInput"
        :disabled="disabled"
        placeholder="owner/repo"
      />
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
