<script setup>
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

import TodoPlan from './TodoPlan.vue'

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
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'text')
}

function todoChunks() {
  return (props.message.chunks || []).filter((chunk) => chunk.kind === 'todo')
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
