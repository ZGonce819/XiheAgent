<script setup>
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  narrations: {
    type: Array,
    default: () => [],
  },
  startedAt: {
    type: Number,
    default: null,
  },
  finishedAt: {
    type: Number,
    default: null,
  },
})

const visibleItems = computed(() => props.items)
const expanded = ref(props.items.some((item) => item.status === 'in_progress'))
const now = ref(Date.now())
let timer = null
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })

const isRunning = computed(() => props.items.some((item) => item.status === 'in_progress'))
const hasError = computed(() => props.items.some((item) => item.status === 'error'))
const activeItem = computed(() => [...props.items].reverse().find((item) => item.status === 'in_progress'))
const elapsedMs = computed(() => {
  if (!props.startedAt) return null
  return Math.max(0, (props.finishedAt || now.value) - props.startedAt)
})

const summary = computed(() => {
  if (isRunning.value) return activeItem.value?.title || '正在执行任务'
  if (hasError.value) return '任务执行失败'
  return '所有任务已执行完成'
})

function formatElapsed(value) {
  if (value == null) return ''
  const totalSeconds = Math.max(0, Math.round(value / 1000))
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes < 60) return `${minutes} 分 ${seconds} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
}

function syncTimer(running) {
  if (timer) clearInterval(timer)
  timer = running ? setInterval(() => { now.value = Date.now() }, 1000) : null
}

watch(isRunning, (running, wasRunning) => {
  syncTimer(running)
  if (running && !wasRunning) expanded.value = true
  if (!running && wasRunning) expanded.value = false
}, { immediate: true })

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

function stateIcon(status) {
  if (status === 'completed') return '✓'
  if (status === 'error') return '×'
  return ''
}

function narrationText(narration) {
  return typeof narration === 'string' ? narration : String(narration?.text || '')
}

function renderMarkdown(narration) {
  return DOMPurify.sanitize(markdown.render(narrationText(narration)))
}
</script>

<template>
  <section
    v-if="visibleItems.length || narrations.length"
    class="process-activity"
    :class="{ running: isRunning, failed: hasError }"
    aria-label="任务执行流程"
  >
    <button
      type="button"
      class="process-activity-toggle"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span>{{ summary }}</span>
      <span v-if="elapsedMs != null" class="process-elapsed">耗时 {{ formatElapsed(elapsedMs) }}</span>
      <span class="activity-chevron" :class="{ expanded }" aria-hidden="true"></span>
    </button>

    <div v-if="expanded" class="process-steps">
      <div
        v-for="item in visibleItems"
        :key="item.id"
        class="process-step"
        :class="item.status"
      >
        <span class="process-step-icon" aria-hidden="true">
          <span v-if="item.status === 'in_progress'" class="process-spinner"></span>
          <span v-else>{{ stateIcon(item.status) }}</span>
        </span>
        <span>{{ item.title }}</span>
      </div>
      <div v-if="narrations.length" class="process-narrations">
        <div
          v-for="(narration, index) in narrations"
          :key="narration.id || index"
          class="process-narration"
          v-html="renderMarkdown(narration)"
        ></div>
      </div>
    </div>
  </section>
</template>
