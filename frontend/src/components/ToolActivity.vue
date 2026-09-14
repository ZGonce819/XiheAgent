<script setup>
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  items: {
    type: Array,
    default: () => [],
  },
  narrations: {
    type: Array,
    default: () => [],
  },
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const isRunning = computed(() => props.items.some((item) => ['pending', 'in_progress'].includes(item.status)))
const errorCount = computed(() => props.items.filter((item) => item.status === 'error').length)
const completedCount = computed(() => props.items.filter((item) => item.status === 'completed').length)
const hasMixedOutcome = computed(() => errorCount.value > 0 && completedCount.value > 0)
const expanded = ref(isRunning.value)
const userInteracted = ref(false)

watch(isRunning, (running, wasRunning) => {
  if (running) expanded.value = true
  if (!running && wasRunning && !userInteracted.value) expanded.value = false
})

function statusLabel(status) {
  if (status === 'completed') return '已完成'
  if (status === 'error') return '失败'
  if (status === 'pending') return '等待中'
  return '执行中'
}

function fileName(path) {
  if (typeof path !== 'string') return ''
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '')
  return normalized.split('/').filter(Boolean).pop() || normalized
}

function toolTitle(item) {
  const title = String(item?.title || '工具调用')
  const isReadFile = item?.toolName === 'read_file'
    || item?.kind === 'read'
    || title.startsWith('读取文件')
  if (!isReadFile) return title

  const name = fileName(item?.args?.path || item?.args?.file_path)
  return name ? `读取文件(${name})` : title.replace(/\(\)$/, '')
}

function activitySummary() {
  if (!props.items.length) return '执行过程'
  if (props.items.length === 1) return toolTitle(props.items[0])
  if (isRunning.value) {
    const active = [...props.items].reverse().find((item) => item.status === 'in_progress')
    return active ? toolTitle(active) : '正在执行工具'
  }
  if (errorCount.value) return `${props.items.length} 项操作，${errorCount.value} 项失败`
  return `已执行 ${completedCount.value || props.items.length} 项操作`
}

function activityMeta() {
  if (!props.items.length) return `${props.narrations.length} 条过程记录`
  if (props.items.length !== 1) return `${props.items.length} 条工具记录`
  const item = props.items[0]
  const elapsed = formatElapsed(item.elapsedMs)
  return elapsed ? `${statusLabel(item.status)} · ${elapsed}` : statusLabel(item.status)
}

function formatElapsed(elapsedMs) {
  if (elapsedMs == null) return ''
  if (elapsedMs < 1000) return `${elapsedMs} ms`
  return `${(elapsedMs / 1000).toFixed(elapsedMs < 10_000 ? 1 : 0)} s`
}

function formatValue(value) {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function argumentSummary(args) {
  if (!args || typeof args !== 'object') return ''
  const preferredKeys = ['path', 'file_path', 'command', 'query', 'url', 'repo']
  const key = preferredKeys.find((name) => args[name] != null) || Object.keys(args)[0]
  if (!key) return ''
  const value = formatValue(args[key]).replace(/\s+/g, ' ').trim()
  return value.length > 180 ? `${value.slice(0, 180)}...` : value
}

function renderMarkdown(text) {
  return DOMPurify.sanitize(markdown.render(String(text || '')))
}
</script>

<template>
  <section
    class="tool-activity"
    :class="{
      running: isRunning,
      warning: hasMixedOutcome,
      failed: errorCount > 0 && !hasMixedOutcome,
    }"
  >
    <button
      type="button"
      class="tool-activity-toggle"
      :aria-expanded="expanded"
      @click="userInteracted = true; expanded = !expanded"
    >
      <span class="tool-activity-state" :class="{ spinning: isRunning }" aria-hidden="true"></span>
      <span class="tool-activity-summary">
        <strong>{{ activitySummary() }}</strong>
        <small>{{ activityMeta() }}</small>
      </span>
      <span class="activity-chevron" :class="{ expanded }" aria-hidden="true"></span>
    </button>

    <div v-if="expanded" class="tool-records">
      <div v-if="narrations.length" class="tool-activity-narrations">
        <div
          v-for="(narration, index) in narrations"
          :key="index"
          class="tool-activity-narration"
          v-html="renderMarkdown(narration)"
        ></div>
      </div>
      <div v-for="item in items" :key="item.id" class="tool-record">
        <span class="tool-record-status" :class="item.status" aria-hidden="true">
          <span v-if="item.status === 'completed'">✓</span>
          <span v-else-if="item.status === 'error'">×</span>
          <span v-else-if="item.status === 'in_progress'" class="mini-spinner"></span>
          <span v-else>·</span>
        </span>
        <div class="tool-record-content">
          <div class="tool-record-title-row">
            <span class="tool-record-title">{{ toolTitle(item) }}</span>
            <span class="tool-record-label">{{ statusLabel(item.status) }}</span>
            <time v-if="item.elapsedMs != null">{{ formatElapsed(item.elapsedMs) }}</time>
          </div>
          <code v-if="argumentSummary(item.args)" class="tool-record-argument">{{ argumentSummary(item.args) }}</code>
          <p v-if="item.status === 'error' && item.detailText" class="tool-record-error">{{ item.detailText }}</p>
        </div>
      </div>
    </div>
  </section>
</template>
