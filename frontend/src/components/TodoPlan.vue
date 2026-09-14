<script setup>
import { computed } from 'vue'

const props = defineProps({
  todos: {
    type: Array,
    default: () => [],
  },
})

const completed = computed(() => props.todos.filter((item) => item.status === 'completed').length)
const active = computed(() => props.todos.find((item) => item.status === 'in_progress'))
const progress = computed(() => props.todos.length ? Math.round((completed.value / props.todos.length) * 100) : 0)

function icon(status) {
  if (status === 'completed') return '✓'
  if (status === 'in_progress') return '↻'
  return '○'
}
</script>

<template>
  <section class="todo-card">
    <header>
      <span class="todo-list-icon" aria-hidden="true">☷</span>
      <div class="todo-heading">
        <strong>任务计划</strong>
        <small v-if="active">正在进行：{{ active.content }}</small>
        <small v-else>{{ completed }}/{{ todos.length }} 项已完成</small>
      </div>
      <span class="todo-count">{{ completed }}/{{ todos.length }}</span>
    </header>
    <div class="todo-progress" aria-hidden="true">
      <span :style="{ width: `${progress}%` }"></span>
    </div>
    <ol>
      <li
        v-for="(todo, index) in todos"
        :key="`${index}-${todo.content}`"
        :class="todo.status"
      >
        <span class="todo-icon">{{ icon(todo.status) }}</span>
        <span>{{ index + 1 }}. {{ todo.content }}</span>
      </li>
    </ol>
  </section>
</template>
