<script setup>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import TodoPlan from './TodoPlan.vue'
import ToolActivity from './ToolActivity.vue'
import ProcessActivity from './ProcessActivity.vue'

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
})

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
</script>

<template>
  <article class="message" :class="`message-${message.author}`">
    <div v-if="message.author === 'user'" class="user-bubble">
      <div
        v-for="(chunk, index) in textChunks()"
        :key="`text-${index}`"
        class="plain-text"
      >
        {{ chunk.text }}
      </div>
    </div>

    <template v-else>
      <ProcessActivity
        v-for="(chunk, index) in processChunks()"
        :key="`process-${index}`"
        :items="chunk.items || []"
        :narrations="chunk.narrations || []"
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
        :key="`todo-${index}`"
        :todos="chunk.todos || []"
      />

      <ToolActivity
        v-for="(chunk, index) in toolChunks()"
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
