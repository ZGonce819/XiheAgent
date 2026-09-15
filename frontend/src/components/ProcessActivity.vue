<script setup>
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import TodoPlan from './TodoPlan.vue'
import ToolActivity from './ToolActivity.vue'

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  narrations: {
    type: Array,
    default: () => [],
  },
  timeline: {
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
const expanded = ref(props.items.some((item) => item.status === 'in_progress') || props.timeline.some((entry) => (
  entry.type === 'tools' && (entry.items || []).some((item) => ['pending', 'in_progress'].includes(item.status))
)))
const userInteracted = ref(false)
const now = ref(Date.now())
let timer = null
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })

const isRunning = computed(() => props.items.some((item) => item.status === 'in_progress'))
const timelineToolItems = computed(() => props.timeline
  .filter((entry) => entry.type === 'tools')
  .flatMap((entry) => entry.items || []))
const isTimelineRunning = computed(() => timelineToolItems.value.some((item) => ['pending', 'in_progress'].includes(item.status)))
const hasError = computed(() => props.items.some((item) => item.status === 'error') || timelineToolItems.value.some((item) => item.status === 'error'))
const activityRunning = computed(() => isRunning.value || isTimelineRunning.value)
const activeItem = computed(() => [...props.items].reverse().find((item) => item.status === 'in_progress'))
const elapsedMs = computed(() => {
  if (!props.startedAt) return null
  return Math.max(0, (props.finishedAt || now.value) - props.startedAt)
})

const summary = computed(() => {
  if (isRunning.value || isTimelineRunning.value) return activeItem.value?.title || '正在执行任务'
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

watch(activityRunning, (running, wasRunning) => {
  syncTimer(running)
  if (running && !wasRunning) expanded.value = true
  if (!running && wasRunning && !userInteracted.value) expanded.value = false
}, { immediate: true })

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})

function stateIcon(status) {
  if (status === 'completed') return '✓'
  if (status === 'error') return '×'
  return ''
}

function stepTitle(entry) {
  if (String(entry?.id || '').endsWith(':model')) return '调用大模型'
  return entry?.title || ''
}

function narrationText(narration) {
  return typeof narration === 'string' ? narration : String(narration?.text || '')
}

function renderMarkdown(narration) {
  return DOMPurify.sanitize(markdown.render(narrationText(narration)))
}

function timelineItems() {
  if (props.timeline.length) return props.timeline
  return [
    ...props.items.map((item) => ({ ...item, type: 'step' })),
    ...props.narrations.map((item) => ({ ...item, type: 'text' })),
  ]
}
</script>

<template>
  <section
    v-if="visibleItems.length || narrations.length || timeline.length"
    class="process-activity"
    :class="{ running: activityRunning, failed: hasError }"
    aria-label="任务执行流程"
  >
    <button
      type="button"
      class="process-activity-toggle"
      :aria-expanded="expanded"
      @click="userInteracted = true; expanded = !expanded"
    >
      <span>{{ summary }}</span>
      <span v-if="elapsedMs != null" class="process-elapsed">耗时 {{ formatElapsed(elapsedMs) }}</span>
      <span class="activity-chevron" :class="{ expanded }" aria-hidden="true"></span>
    </button>

    <div v-if="expanded" class="process-steps">
      <template v-for="entry in timelineItems()" :key="entry.id">
        <div v-if="entry.type === 'step'" class="process-step" :class="entry.status">
          <span class="process-step-icon" aria-hidden="true">
            <span v-if="entry.status === 'in_progress'" class="process-spinner"></span>
            <span v-else>{{ stateIcon(entry.status) }}</span>
          </span>
          <span>{{ stepTitle(entry) }}</span>
        </div>
        <div
          v-else-if="entry.type === 'text'"
          class="process-narration"
          v-html="renderMarkdown(entry.text)"
        ></div>
        <ToolActivity
          v-else-if="entry.type === 'tools'"
          :items="entry.items || []"
          :narrations="entry.narrations || []"
        />
        <TodoPlan
          v-else-if="entry.type === 'todo'"
          :todos="entry.todos || []"
          embedded
        />
      </template>
    </div>
  </section>
</template>
