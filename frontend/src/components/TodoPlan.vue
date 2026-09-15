<script setup>
import { computed } from 'vue'

const props = defineProps({
  todos: {
    type: Array,
    default: () => [],
  },
  embedded: {
    type: Boolean,
    default: false,
  },
})

const completed = computed(() => props.todos.filter((item) => item.status === 'completed').length)
const active = computed(() => props.todos.find((item) => item.status === 'in_progress'))
const failed = computed(() => props.todos.filter((item) => item.status === 'error').length)
const progress = computed(() => props.todos.length ? Math.round((completed.value / props.todos.length) * 100) : 0)
const planState = computed(() => {
  if (active.value) return 'running'
  if (failed.value) return 'failed'
  if (props.todos.length && completed.value === props.todos.length) return 'complete'
  return 'pending'
})
const summary = computed(() => {
  if (active.value) return `正在执行第 ${props.todos.indexOf(active.value) + 1} 项`
  if (failed.value) return `${failed.value} 项执行失败`
  if (props.todos.length && completed.value === props.todos.length) return '计划已全部完成'
  return `${props.todos.length - completed.value} 项待处理`
})

function icon(status) {
  if (status === 'completed') return '✓'
  if (status === 'error') return '×'
  return ''
}

function statusLabel(status) {
  if (status === 'completed') return '已完成'
  if (status === 'in_progress') return '进行中'
  if (status === 'error') return '失败'
  return '待处理'
}

function itemNumber(index) {
  return String(index + 1).padStart(2, '0')
}
</script>

<template>
  <section
    v-if="todos.length"
    class="todo-plan"
    :class="[planState, { embedded }]"
    aria-label="任务计划"
  >
    <header class="todo-plan-header">
      <div class="todo-plan-title">
        <span class="todo-plan-mark" aria-hidden="true"></span>
        <div>
          <strong>任务计划</strong>
          <small aria-live="polite">{{ summary }}</small>
        </div>
      </div>
      <div class="todo-plan-count" :aria-label="`已完成 ${completed} 项，共 ${todos.length} 项`">
        <span><strong>{{ completed }}</strong> / {{ todos.length }}</span>
        <small>{{ progress }}%</small>
      </div>
    </header>
    <div
      class="todo-plan-progress"
      role="progressbar"
      :aria-valuenow="progress"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="`任务完成度 ${progress}%`"
    >
      <span :style="{ width: `${progress}%` }"></span>
    </div>
    <ol class="todo-plan-items">
      <li
        v-for="(todo, index) in todos"
        :key="`${index}-${todo.content}`"
        :class="todo.status"
        :aria-current="todo.status === 'in_progress' ? 'step' : undefined"
      >
        <span class="todo-plan-node" aria-hidden="true">
          <span v-if="todo.status === 'in_progress'" class="todo-plan-spinner"></span>
          <span v-else>{{ icon(todo.status) }}</span>
        </span>
        <span class="todo-plan-number">{{ itemNumber(index) }}</span>
        <span class="todo-plan-content">{{ todo.content }}</span>
        <span class="todo-plan-status">{{ statusLabel(todo.status) }}</span>
      </li>
    </ol>
  </section>
</template>
