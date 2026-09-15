<script setup>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import TodoPlan from './TodoPlan.vue'
import ToolActivity from './ToolActivity.vue'
import ProcessActivity from './ProcessActivity.vue'
import { ref } from 'vue'

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
  editable: { type: Boolean, default: false },
  modelOptions: { type: Array, default: () => [] },
  selectedModel: { type: String, default: '' },
})
const emit = defineEmits(['edit', 'model-change'])
const editing = ref(false)
const draft = ref('')

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

function renderMarkdown(text) {
  return DOMPurify.sanitize(markdown.render(text || ''))
}

function textChunks() {
  const chunks = props.message.chunks || []
  const hasTodo = chunks.some((chunk) => chunk.kind === 'todo')
  return chunks.filter((chunk) => (
    chunk.kind === 'text' && !(hasTodo && String(chunk.text || '').trim() === '任务计划')
  ))
}

function todoChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'todo')
}

function toolChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'tools')
}

function processChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'process')
}

function errorChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'error')
}

function hasEmbeddedTimelineEntry(type) {
  return processChunks().some((chunk) => (chunk.timeline || []).some((entry) => entry.type === type))
}

function userText() {
  return textChunks().map((chunk) => String(chunk.text || '')).join('')
}

function startEditing() {
  draft.value = userText()
  editing.value = true
}

function cancelEditing() {
  editing.value = false
}

function submitEditing() {
  const value = draft.value.trim()
  if (!value) return
  editing.value = false
  emit('edit', { message: props.message, content: value })
}

function formatTimestamp(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function copyMessage() {
  try {
    await navigator.clipboard?.writeText(userText())
  } catch {
    // Clipboard access may be unavailable outside a secure browser context.
  }
}
</script>

<template>
  <article class="message" :class="`message-${message.author}`">
    <div v-if="message.author === 'user'" class="user-message-wrap">
      <div v-if="editing" class="user-edit-box">
        <textarea v-model="draft" autofocus class="user-edit-input" @keydown.enter.exact.prevent="submitEditing" />
        <div class="user-edit-actions">
          <select v-if="modelOptions.length" class="message-model-select" :value="selectedModel" aria-label="选择模型" @change="emit('model-change', $event.target.value)">
            <option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.label || model.id }}</option>
          </select>
          <button type="button" class="message-action-button" @click="cancelEditing">取消</button>
          <button type="button" class="message-action-button message-action-primary" :disabled="!draft.trim()" @click="submitEditing">发送</button>
        </div>
      </div>
      <div v-else class="user-bubble">
        <div
          v-for="(chunk, index) in textChunks()"
          :key="`text-${index}`"
          class="plain-text"
        >
          {{ chunk.text }}
        </div>
      </div>
      <div class="message-actions" aria-label="消息操作">
        <time>{{ formatTimestamp(message.timestamp) }}</time>
        <button type="button" class="message-icon-button" title="复制" aria-label="复制" @click="copyMessage">
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>
        </button>
        <button v-if="editable" type="button" class="message-icon-button" title="编辑并重新发送" aria-label="编辑并重新发送" @click="startEditing">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 16.5V20h3.5L18.8 8.7a2.1 2.1 0 0 0-3-3L4 16.5Z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m14.5 7.5 2 2" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>
        </button>
      </div>
    </div>

    <template v-else>
      <ProcessActivity
        v-for="(chunk, index) in processChunks()"
        :key="`process-${index}`"
        :items="chunk.items || []"
        :narrations="chunk.narrations || []"
        :timeline="chunk.timeline || []"
        :started-at="chunk.startedAt"
        :finished-at="chunk.finishedAt"
      />

      <div
        v-for="(chunk, index) in textChunks()"
        :key="`text-${index}`"
        class="markdown-body"
        v-html="renderMarkdown(chunk.text)"
      ></div>

      <TodoPlan
        v-for="(chunk, index) in todoChunks()"
        v-if="!hasEmbeddedTimelineEntry('todo')"
        :key="`todo-${index}`"
        :todos="chunk.todos || []"
      />

      <ToolActivity
        v-for="(chunk, index) in toolChunks()"
        v-if="!hasEmbeddedTimelineEntry('tools')"
        :key="`tools-${index}`"
        :items="chunk.items || []"
        :narrations="chunk.narrations || []"
      />

      <div
        v-for="(chunk, index) in errorChunks()"
        :key="`error-${index}`"
        class="error-banner"
      >
        {{ chunk.text }}
      </div>
    </template>
  </article>
</template>
