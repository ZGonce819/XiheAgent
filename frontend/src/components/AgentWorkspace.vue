<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'

import ChatComposer from './ChatComposer.vue'
import ChatMessage from './ChatMessage.vue'
import SessionSidebar from './SessionSidebar.vue'
import { useAgentStore } from '../stores/agent'

const agent = useAgentStore()
const messageList = ref(null)

watch(
  () => agent.messages.map((message) => {
    const length = message.chunks?.map((chunk) => {
      if (chunk.kind === 'todo') return JSON.stringify(chunk.todos || [])
      return chunk.text || ''
    }).join('').length || 0
    return `${message.id}:${length}`
  }).join('|'),
  async () => {
    await nextTick()
    if (messageList.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
  },
)

onMounted(() => {
  agent.bootstrap()
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
          <h1>xihe-agent</h1>
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
        <div v-if="agent.loading" class="empty-state">正在加载会话...</div>
        <div v-else-if="!agent.messages.length" class="empty-state">
          <div class="empty-icon">⚡</div>
          <h2>让 xihe-agent 构建、修复或检查代码仓库</h2>
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
        />

        <div v-if="agent.error" class="error-banner">{{ agent.error }}</div>
      </div>

      <ChatComposer
        v-model:repo="agent.selectedRepo"
        v-model:platform="agent.selectedPlatform"
        v-model:model="agent.selectedModel"
        :disabled="agent.streaming"
        :effort="agent.selectedEffort"
        @send="agent.submit"
        @stop="agent.stopStream"
      />
    </section>
  </main>
</template>
