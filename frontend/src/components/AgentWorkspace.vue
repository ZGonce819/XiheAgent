<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ChatComposer from './ChatComposer.vue'
import ChatMessage from './ChatMessage.vue'
import SessionSidebar from './SessionSidebar.vue'
import { useAgentStore } from '../stores/agent'

const agent = useAgentStore()
const messageList = ref(null)
const showScrollToBottom = ref(false)
const stickToBottom = ref(true)

function updateScrollState() {
  const el = messageList.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  showScrollToBottom.value = distance > 48
  stickToBottom.value = distance <= 48
}

function scrollToBottom() {
  const el = messageList.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  stickToBottom.value = true
  showScrollToBottom.value = false
}

watch(
  () => agent.messages.map((message) => {
    const signature = message.chunks?.map((chunk) => {
      if (chunk.kind === 'todo') return JSON.stringify(chunk.todos || [])
      if (chunk.kind === 'tools') {
        return JSON.stringify({
          items: (chunk.items || []).map((item) => [item.id, item.status, item.elapsedMs]),
          narrations: chunk.narrations || [],
        })
      }
      if (chunk.kind === 'process') {
        return JSON.stringify({
          items: (chunk.items || []).map((item) => [item.id, item.title, item.status]),
          narrations: chunk.narrations || [],
          timeline: (chunk.timeline || []).map((entry) => ({
            id: entry.id,
            type: entry.type,
            text: entry.text,
            items: (entry.items || []).map((item) => [item.id, item.status, item.elapsedMs]),
            todos: (entry.todos || []).map((todo) => [todo.content, todo.status]),
          })),
        })
      }
      return chunk.text || ''
    }).join('|') || ''
    return `${message.id}:${signature}`
  }).join('|'),
  async () => {
    await nextTick()
    if (messageList.value && stickToBottom.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
    updateScrollState()
  },
)

onMounted(() => {
  agent.bootstrap()
  messageList.value?.addEventListener('scroll', updateScrollState, { passive: true })
})

onBeforeUnmount(() => {
  messageList.value?.removeEventListener('scroll', updateScrollState)
})
</script>

<template>
  <main class="app-shell">
    <SessionSidebar
      :threads="agent.threads"
      :active-id="agent.currentThreadId"
      :disabled="agent.streaming"
      @new-thread="agent.createThread"
      @select-thread="agent.selectThread"
      @delete-thread="agent.deleteThread"
    />

    <section class="workspace">
      <header class="workspace-header">
        <div>
          <h1>XiheAgent</h1>
          <p>
            <span class="dot" :class="{ running: agent.streaming }"></span>
            {{ agent.streaming ? 'Agent 正在运行' : 'FastAPI + DeepAgents' }}
          </p>
        </div>
        <div class="thread-meta" v-if="agent.currentThread">
          <span>状态：{{ agent.currentThread.status }}</span>
          <span v-if="agent.currentThread.branch">分支：{{ agent.currentThread.branch }}</span>
          <a v-if="agent.currentThread.pr?.url" :href="agent.currentThread.pr.url" target="_blank">PR</a>
        </div>
      </header>

      <div ref="messageList" class="message-list">
        <div class="message-list-content">
        <div v-if="agent.loading" class="empty-state">正在加载会话...</div>
        <div v-else-if="!agent.messages.length" class="empty-state">
          <div class="empty-icon">⚡</div>
          <h2>让 XiheAgent 构建、修复或检查代码仓库</h2>
          <p>输入技术方案、代码实施、PR 审查等指令，前端会按顺序展示用户输入、任务计划和 AI 输出。</p>
          <div class="empty-hints">
            <div class="hint-item">
              <span class="hint-icon">📝</span>
              <span>代码生成与重构</span>
            </div>
            <div class="hint-item">
              <span class="hint-icon">🔍</span>
              <span>代码审查与分析</span>
            </div>
            <div class="hint-item">
              <span class="hint-icon">🚀</span>
              <span>自动化 PR 流程</span>
            </div>
          </div>
        </div>

        <ChatMessage
          v-for="message in agent.messages"
          :key="message.id"
          :message="message"
          :editable="!agent.streaming && message.author === 'user' && message.id === agent.latestUserMessageId"
          :model-options="agent.modelOptions"
          :selected-model="agent.selectedModel"
          @edit="agent.editMessage"
          @model-change="agent.selectedModel = $event"
        />

        <div v-if="agent.error" class="error-banner">{{ agent.error }}</div>
        </div>
      </div>

      <div class="composer-dock">
        <button
          v-if="showScrollToBottom"
          type="button"
          class="scroll-to-bottom"
          :class="{ loading: agent.streaming }"
          aria-label="回到底部"
          @click="scrollToBottom"
        >
          <span class="scroll-to-bottom-arrow" aria-hidden="true"></span>
        </button>

        <ChatComposer
          v-model:repo="agent.selectedRepo"
          v-model:platform="agent.selectedPlatform"
          v-model:model="agent.selectedModel"
          :disabled="agent.streaming"
          :locked="!!agent.currentThread"
          :effort="agent.selectedEffort"
          @send="agent.submit"
          @stop="agent.stopStream"
        />
      </div>
    </section>

    <div v-if="agent.confirmDialog" class="dialog-backdrop" role="presentation" @click.self="agent.resolveConfirm(false)">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <h2 id="confirm-dialog-title">{{ agent.confirmDialog.title }}</h2>
        <p class="confirm-dialog-message">{{ agent.confirmDialog.message }}</p>
        <div class="confirm-dialog-actions">
          <button type="button" class="dialog-button dialog-button-secondary" @click="agent.resolveConfirm(false)">取消</button>
          <button type="button" class="dialog-button dialog-button-primary" @click="agent.resolveConfirm(true)">确定</button>
        </div>
      </section>
    </div>
  </main>
</template>
