import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { agentsApi } from "./api";
import { agentThreadKeys } from "./queries";
import type {
  AgentStatus,
  AgentThread,
  Chunk,
  Message,
  TextChunk,
  TodoItem,
} from "./types";

interface StreamPart {
  event?: string;
  data?: unknown;
}

interface ThreadMetaPayload {
  id?: string;
  status?: AgentStatus;
  branch?: string;
  repoUrl?: string;
  platform?: string;
  platformDisplay?: string;
  pr?: AgentThread["pr"];
  updatedAt?: number;
}

interface TextDeltaPayload {
  message_id?: string;
  run_id?: string;
  content?: string;
  mode?: "append" | "replace";
}

interface UserMessagePayload {
  message_id?: string;
  run_id?: string;
  content?: string;
  timestamp?: string;
}

interface TodoDeltaPayload {
  message_id?: string;
  run_id?: string;
  todos?: Array<TodoItem>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function updateThread(
  threadId: string,
  queryClient: QueryClient,
  updater: (thread: AgentThread) => AgentThread,
) {
  const key = agentThreadKeys.detail(threadId);
  void queryClient.cancelQueries({ queryKey: key });
  queryClient.setQueryData<AgentThread | undefined>(key, (prev) =>
    prev ? updater(prev) : prev,
  );
}

function invalidateThreadList(queryClient: QueryClient) {
  // `agentThreadKeys.all` 是 thread 列表 key，也是 detail key 的前缀。
  // 流式运行结束时只能刷新侧边栏列表，不能让当前 detail 重新请求并覆盖
  // SSE 已经追加好的 messages。
  void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
}

function upsertMessage(thread: AgentThread, message: Message): AgentThread {
  const index = thread.messages.findIndex((item) => item.id === message.id);
  if (index < 0) {
    return { ...thread, messages: [...thread.messages, message] };
  }
  const messages = thread.messages.slice();
  messages[index] = message;
  return { ...thread, messages };
}

function runIdFromMessageId(messageId: string): string | null {
  const patterns = [
    /-user-(.+)$/,
    /-live-plan-(.+)$/,
    /-live-assistant-(.+)$/,
  ];
  for (const pattern of patterns) {
    const match = messageId.match(pattern);
    if (match?.[1]) return match[1];
  }
  return null;
}

function payloadRunId(data: { run_id?: string; message_id?: string }): string | null {
  return data.run_id || (data.message_id ? runIdFromMessageId(data.message_id) : null);
}

function findRunMessageIndex(
  messages: Array<Message>,
  runId: string | null,
  role: "user" | "plan" | "assistant",
): number {
  if (!runId) return -1;
  const marker =
    role === "user"
      ? `-user-${runId}`
      : role === "plan"
        ? `-live-plan-${runId}`
        : `-live-assistant-${runId}`;
  return messages.findIndex((message) => message.id.endsWith(marker));
}

function insertOrReplaceAt(messages: Array<Message>, message: Message, insertAt: number): Array<Message> {
  const withoutCurrent = messages.filter((item) => item.id !== message.id);
  const boundedIndex = Math.min(Math.max(insertAt, 0), withoutCurrent.length);
  const next = withoutCurrent.slice();
  next.splice(boundedIndex, 0, message);
  return next;
}

function ensureAgentMessage(thread: AgentThread, messageId: string): Message {
  const existing = thread.messages.find((message) => message.id === messageId);
  if (existing) return existing;
  return {
    id: messageId,
    author: "agent",
    timestamp: new Date().toISOString(),
    chunks: [],
  };
}

function setTextChunk(message: Message, text: string, mode: "append" | "replace"): Message {
  const chunks = message.chunks.slice();
  const textIndex = chunks.findIndex((chunk) => chunk.kind === "text");
  if (textIndex >= 0) {
    const previous = chunks[textIndex] as TextChunk;
    chunks[textIndex] = {
      ...previous,
      text: mode === "replace" ? text : previous.text + text,
    };
  } else {
    chunks.push({ kind: "text", text });
  }
  return { ...message, chunks };
}

function applyTextDelta(thread: AgentThread, data: TextDeltaPayload): AgentThread {
  const messageId = data.message_id;
  const content = data.content ?? "";
  if (!messageId || !content) return thread;
  const message = ensureAgentMessage(thread, messageId);
  const nextMessage = setTextChunk(message, content, data.mode ?? "append");
  const existingIndex = thread.messages.findIndex((item) => item.id === messageId);
  if (existingIndex >= 0) {
    return upsertMessage(thread, nextMessage);
  }

  const runId = payloadRunId(data);
  const messages = thread.messages.slice();
  const planIndex = findRunMessageIndex(messages, runId, "plan");
  const userIndex = findRunMessageIndex(messages, runId, "user");
  const insertAt =
    planIndex >= 0
      ? planIndex + 1
      : userIndex >= 0
        ? userIndex + 1
        : messages.length;
  return { ...thread, messages: insertOrReplaceAt(messages, nextMessage, insertAt) };
}

function applyUserMessage(thread: AgentThread, data: UserMessagePayload): AgentThread {
  const content = data.content?.trim();
  if (!content) return thread;
  const messageId = data.message_id || `user-${Date.now()}`;

  // 正式 SSE user_message 代表一个 run 的真实用户输入，只能按 message_id 去重。
  // 不要按文本去重：多轮对话里用户可能反复输入“确认”“开始实施”，文本相同
  // 也必须作为独立轮次保留。
  if (thread.messages.some((message) => message.id === messageId)) return thread;

  const userMessage: Message = {
    id: messageId,
    author: "user",
    timestamp: data.timestamp || new Date().toISOString(),
    chunks: [{ kind: "text", text: content }],
  };
  const runId = payloadRunId(data);
  const messages = thread.messages.slice();
  const planIndex = findRunMessageIndex(messages, runId, "plan");
  const assistantIndex = findRunMessageIndex(messages, runId, "assistant");
  const insertAt =
    planIndex >= 0
      ? planIndex
      : assistantIndex >= 0
        ? assistantIndex
        : messages.length;
  return { ...thread, messages: insertOrReplaceAt(messages, userMessage, insertAt) };
}

function applyTodoDelta(thread: AgentThread, data: TodoDeltaPayload): AgentThread {
  const messageId = data.message_id;
  const todos = data.todos ?? [];
  if (!messageId || todos.length === 0) return thread;

  const current = ensureAgentMessage(thread, messageId);
  const todoChunk: Chunk = { kind: "todo", todos };
  const chunks: Array<Chunk> = [
    { kind: "text", text: "任务计划" },
    todoChunk,
  ];

  const planMessage: Message = {
    ...current,
    author: "agent",
    chunks,
  };

  const withoutOldPlan = thread.messages.filter((message) => message.id !== messageId);
  // 任务计划属于某一轮 run，必须插到同 run 的 user_message 后面。
  // 不能再用“最后一条用户消息”猜位置，因为第二轮用户输入可能还只是 pending prompt，
  // 不在 thread.messages 里，会导致第二轮任务计划插到上一轮内容前面。
  const runId = payloadRunId(data);
  const userIndex = findRunMessageIndex(withoutOldPlan, runId, "user");
  const assistantIndex = findRunMessageIndex(withoutOldPlan, runId, "assistant");
  const insertAt =
    userIndex >= 0
      ? userIndex + 1
      : assistantIndex >= 0
        ? assistantIndex
        : withoutOldPlan.length;
  return { ...thread, messages: insertOrReplaceAt(withoutOldPlan, planMessage, insertAt) };
}

function applyThreadMeta(thread: AgentThread, data: ThreadMetaPayload): AgentThread {
  return {
    ...thread,
    status: data.status ?? thread.status,
    branch: data.branch ?? thread.branch,
    repoUrl: data.repoUrl ?? thread.repoUrl,
    platform: data.platform ?? thread.platform,
    platformDisplay: data.platformDisplay ?? thread.platformDisplay,
    pr: data.pr ?? thread.pr,
    updatedAt: data.updatedAt ?? thread.updatedAt,
  };
}

function handleStreamPart(
  threadId: string,
  part: StreamPart,
  queryClient: QueryClient,
) {
  const event = part.event ?? "";
  const data = isRecord(part.data) ? part.data : {};

  if (event === "thread_snapshot" || event === "thread_done") {
    updateThread(threadId, queryClient, (thread) =>
      applyThreadMeta(thread, data as ThreadMetaPayload),
    );
    if (event === "thread_done") {
      invalidateThreadList(queryClient);
    }
    return;
  }

  if (event === "todo_delta") {
    updateThread(threadId, queryClient, (thread) =>
      applyTodoDelta(thread, data as TodoDeltaPayload),
    );
    return;
  }

  if (event === "user_message") {
    updateThread(threadId, queryClient, (thread) =>
      applyUserMessage(thread, data as UserMessagePayload),
    );
    return;
  }

  if (event === "message_start") {
    const messageId = String(data.message_id || "");
    if (!messageId) return;
    updateThread(threadId, queryClient, (thread) =>
      upsertMessage(thread, ensureAgentMessage(thread, messageId)),
    );
    return;
  }

  if (event === "text_delta") {
    updateThread(threadId, queryClient, (thread) =>
      applyTextDelta(thread, data as TextDeltaPayload),
    );
    return;
  }

  if (event === "tool_update") {
    // 工具事件默认只保留在后端日志 / run_events 中，不进入聊天正文。
    // 这样页面重点展示任务计划和有价值的 AIMessage 文本。
    return;
  }

  if (event === "done") {
    updateThread(threadId, queryClient, (thread) => ({ ...thread, status: "finished" }));
    invalidateThreadList(queryClient);
    return;
  }

  if (event === "error") {
    updateThread(threadId, queryClient, (thread) => ({ ...thread, status: "error" }));
  }
}

export function useAgentThreadStream(threadId: string, enabled: boolean) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    let cancelled = false;

    async function consume() {
      try {
        const res = await fetch(agentsApi.streamUrl(threadId), {
          credentials: "include",
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) return;

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf("\n\n");
          while (boundary !== -1) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            for (const line of block.split("\n")) {
              if (!line.startsWith("data: ")) continue;
              const raw = line.slice(6);
              try {
                handleStreamPart(threadId, JSON.parse(raw) as StreamPart, queryClient);
              } catch {
                // 单个事件解析失败时不刷新整条会话，避免旧快照覆盖本地流式内容。
                console.debug("failed to parse agent stream event", raw);
              }
            }
            boundary = buffer.indexOf("\n\n");
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.debug("agent thread stream closed", error);
        }
      }
    }

    void consume();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [enabled, queryClient, threadId]);
}
