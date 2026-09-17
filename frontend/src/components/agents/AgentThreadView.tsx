import { useEffect, useMemo, useState } from "react";

import type { SessionUser } from "@/lib/api";
import type { PendingPrompt } from "@/lib/agents/pendingPrompts";
import type { AgentThread, ImageChunk, Message } from "@/lib/agents/types";
import type { ModelSelection } from "@/lib/agents/useModelOptions";
import { AgentPromptBar } from "@/components/agents/AgentPromptBar";
import { AgentsShell } from "@/components/agents/AgentsSidebar";
import { MessageView } from "@/components/agents/ported";
import { useCancelAgentThread, useSendAgentMessage, useUpdateAgentThreadTitle } from "@/lib/agents/queries";
import { addPendingPrompt, dropPendingPrompts, getPendingPrompts } from "@/lib/agents/pendingPrompts";
import { useAgentThreadStream } from "@/lib/agents/useThreadStream";
import { normalizeThreadHistory } from "@/lib/agents/useThreadStream";
import { useModelOptions } from "@/lib/agents/useModelOptions";
import { useProfile } from "@/lib/profile";
import { agentsApi } from "@/lib/agents/api";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CommandApprovalDialog } from "@/components/agents/CommandApprovalDialog";
import { useDecideCommandApproval, useExplainCommandApproval } from "@/lib/agents/queries";

interface AgentThreadViewProps {
  user: SessionUser;
  thread: AgentThread;
}

interface PageDialog {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  resolve: (confirmed: boolean) => void;
}

function messageText(message: Message): string {
  return message.chunks
    .filter((chunk) => chunk.kind === "text")
    .map((chunk) => chunk.text)
    .join("");
}

function messageImageKey(message: Message): string {
  return message.chunks
    .filter((chunk) => chunk.kind === "image")
    .map((chunk) => `${chunk.mimeType}:${chunk.base64}`)
    .join("\u0000");
}

function pendingImageKey(entry: PendingPrompt): string {
  return (entry.images ?? [])
    .map((image) => `${image.mimeType}:${image.base64}`)
    .join("\u0000");
}

function isPendingPromptConfirmed(entry: PendingPrompt, messages: Array<Message>): boolean {
  // pending 只能被它提交位置之后的新用户消息确认。
  // 不能全历史按文本匹配，否则“确认”“开始实施”这类重复短指令会被旧消息误删。
  const candidateMessages = messages.slice(Math.max(0, entry.insertAt));
  return candidateMessages.some((message) => {
    if (message.author !== "user") return false;
    return messageText(message) === entry.prompt && messageImageKey(message) === pendingImageKey(entry);
  });
}

function liveStatusFor(thread: AgentThread, hasPendingPrompt: boolean): string | null {
  if (thread.status !== "running") return null;

  const activeEvent = [...(thread.runEvents ?? [])].reverse().find(
    (event) => event.status === "in_progress" || event.status === "pending",
  );
  if (activeEvent) return activeEvent.title;

  for (const message of [...thread.messages].reverse()) {
    for (const chunk of [...message.chunks].reverse()) {
      if (chunk.kind !== "tool-execution") continue;
      const status =
        chunk.status === "in_progress"
          ? "运行中"
          : chunk.status === "pending"
            ? "已排队"
            : chunk.status === "error"
              ? "失败"
              : "已完成";
      return `${status}: ${chunk.title}`;
    }
  }

  return hasPendingPrompt ? "正在启动智能体运行..." : "正在等待智能体输出...";
}

export function AgentThreadView({ user, thread }: AgentThreadViewProps) {
  const sendMessage = useSendAgentMessage(thread.id);
  const cancelThread = useCancelAgentThread(thread.id);
  const decideApproval = useDecideCommandApproval(thread.id);
  const explainApproval = useExplainCommandApproval(thread.id);
  useAgentThreadStream(thread.id, thread.status === "running");
  const [pendingPrompts, setPendingPrompts] = useState<Array<PendingPrompt>>(() =>
    getPendingPrompts(thread.id),
  );

  const { models, defaultSelection } = useModelOptions();
  const profile = useProfile();
  const sandboxAvailable = profile.data?.sandbox_available ?? false;
  const [backendType, setBackendType] = useState<string>(
    thread.backendType ?? profile.data?.backendType ?? "local"
  );
  const [sandboxWarning, setSandboxWarning] = useState(false);
  const [repoWarning, setRepoWarning] = useState(false);

  const handleBackendTypeChange = (next: string) => {
    if (next === "sandbox" && !sandboxAvailable) {
      setSandboxWarning(true);
      return;
    }
    setBackendType(next);
  };

  // 仅当沙箱不可用时（USE_SANDBOX=false），环境不匹配才需要提示和限制
  const isBackendMismatch = !sandboxAvailable && (thread.backendType ?? "local") !== backendType;
  const backendMismatchLabel = (thread.backendType ?? "local") === "sandbox" ? "沙箱" : "本地";
  const threadSelection = useMemo<ModelSelection | null>(() => {
    if (!thread.model || !thread.effort) return null;
    const supported = models.some(
      (m) => m.id === thread.model && m.efforts.includes(thread.effort ?? ""),
    );
    if (!supported) return null;
    return { modelId: thread.model, effort: thread.effort };
  }, [models, thread.model, thread.effort]);
  const [selection, setSelection] = useState<ModelSelection | null>(null);
  const [pageDialog, setPageDialog] = useState<PageDialog | null>(null);
  const [viewedVersionIndexes, setViewedVersionIndexes] = useState<Record<string, number>>({});
  const [sendTransition, setSendTransition] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const updateTitle = useUpdateAgentThreadTitle(thread.id);
  const [newThreadHandoff, setNewThreadHandoff] = useState(() => {
    if (typeof window === "undefined") return false;
    const key = `xihe:new-thread-transition:${thread.id}`;
    try {
      const shouldAnimate = window.sessionStorage.getItem(key) === "1";
      window.sessionStorage.removeItem(key);
      return shouldAnimate;
    } catch {
      return false;
    }
  });
  const activeSelection = selection ?? threadSelection ?? defaultSelection;
  const displayRepo = (() => {
    const value = thread.repoFullName || thread.repo || "";
    if (!/^https?:\/\//i.test(value)) return value;
    try {
      const parts = new URL(value).pathname.split("/").filter(Boolean);
      return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : value;
    } catch {
      return value;
    }
  })();
  const completedElapsedMs = useMemo(() => {
    if (thread.status === "running") return 0;
    const events = thread.runEvents ?? [];
    const starts = events.map((event) => Date.parse(event.created_at)).filter(Number.isFinite);
    const ends = events.map((event) => Date.parse(event.updated_at || event.created_at)).filter(Number.isFinite);
    if (starts.length === 0 || ends.length === 0) return 0;
    const elapsed = Math.max(...ends) - Math.min(...starts);
    return elapsed > 0 ? elapsed : 0;
  }, [thread.runEvents, thread.status]);

  const showDialog = (dialog: Omit<PageDialog, "resolve">) =>
    new Promise<boolean>((resolve) => {
      setPageDialog({ ...dialog, resolve });
    });

  useEffect(() => {
    setViewedVersionIndexes({});
  }, [thread.id]);

  useEffect(() => {
    if (newThreadHandoff && thread.status !== "running") {
      setNewThreadHandoff(false);
    }
  }, [newThreadHandoff, thread.status]);

  useEffect(() => {
    if (thread.status === "running") setViewedVersionIndexes({});
  }, [thread.status]);

  useEffect(() => {
    setPendingPrompts((prev) => {
      if (prev.length === 0) return prev;
      if (thread.status !== "running") {
        // 结束时不能无条件清空 pending。若正式 user_message 因网络或后端时序尚未
        // 进入 thread.messages，直接清空会让用户刚输入的指令从页面消失。
        return dropPendingPrompts(thread.id, (entry) =>
          isPendingPromptConfirmed(entry, thread.messages),
        );
      }
      const next = dropPendingPrompts(thread.id, (entry) =>
        isPendingPromptConfirmed(entry, thread.messages),
      );
      return next.length === prev.length ? prev : next;
    });
  }, [thread.id, thread.messages, thread.status]);

  const displayMessages = useMemo<Array<Message>>(() => {
    if (pendingPrompts.length === 0) return thread.messages;
    const result = thread.messages.slice();
    pendingPrompts.forEach((entry, i) => {
      if (isPendingPromptConfirmed(entry, thread.messages)) return;
      const chunks: Message["chunks"] = [...(entry.images ?? [])];
      if (entry.prompt) chunks.push({ kind: "text", text: entry.prompt });
      const synth: Message = {
        id: `pending-user-${entry.id}-${i}`,
        author: "user",
        timestamp: new Date(entry.createdAt || Date.now()).toISOString(),
        chunks,
      };
      const at = Math.min(Math.max(entry.insertAt, 0), result.length);
      result.splice(at, 0, synth);
    });
    return result;
  }, [thread.messages, pendingPrompts]);

  const conversationVersions = useMemo(
    () =>
      (thread.conversationVersions ?? []).map((version) => {
        const normalized = normalizeThreadHistory({
          ...thread,
          messages: version.messages,
          runEvents: version.runEvents,
          conversationVersions: [],
        });
        return {
          ...version,
          messages: normalized.messages,
          runEvents: normalized.runEvents ?? version.runEvents,
          editedUserIndex: version.editedUserIndex ?? Math.max(0, normalized.messages.filter((message) => message.author === "user").length - 1),
        };
      }),
    [thread, thread.conversationVersions],
  );
  const userMessages = displayMessages.filter((message) => message.author === "user");
  const visibleMessages = useMemo(() => {
    let result = displayMessages.slice();
    const activeVersions = Object.entries(viewedVersionIndexes);
    for (const [userIndexText, versionIndex] of activeVersions) {
      const userIndex = Number(userIndexText);
      const version = conversationVersions.filter((item) => item.editedUserIndex === userIndex)[versionIndex];
      if (!version) continue;
      const snapshotUsers = version.messages.filter((message) => message.author === "user");
      const currentUserStart = result.findIndex((message) => message.author === "user" && userMessages.indexOf(message) === userIndex);
      if (currentUserStart < 0) continue;
      const currentUserEnd = result.findIndex((message, index) => index > currentUserStart && message.author === "user");
      const snapshotUser = snapshotUsers[userIndex];
      if (!snapshotUser) continue;
      const snapshotStart = version.messages.findIndex((message) => message.id === snapshotUser.id);
      const snapshotEnd = version.messages.findIndex((message, index) => index > snapshotStart && message.author === "user");
      const replacement = version.messages.slice(snapshotStart, snapshotEnd < 0 ? version.messages.length : snapshotEnd);
      result.splice(currentUserStart, (currentUserEnd < 0 ? result.length : currentUserEnd) - currentUserStart, ...replacement);
    }
    return result;
  }, [displayMessages, conversationVersions, viewedVersionIndexes, userMessages]);
  const isViewingCurrentVersion = Object.keys(viewedVersionIndexes).length === 0;
  const versionNavigations = useMemo(() => {
    const map: Record<string, { position: number; total: number; onPrevious: () => void; onNext: () => void }> = {};
    userMessages.forEach((_userMessage, userIndex) => {
      const versions = conversationVersions.filter((version) => version.editedUserIndex === userIndex);
      if (versions.length === 0) return;
      const currentIndex = viewedVersionIndexes[String(userIndex)] ?? versions.length;
      const visibleUsers = visibleMessages.filter((message) => message.author === "user");
      const visibleUserId = visibleUsers[userIndex]?.id;
      const currentUserStart = visibleUserId
        ? visibleMessages.findIndex((message) => message.id === visibleUserId)
        : -1;
      if (currentUserStart < 0) return;
      const nextUserStart = visibleMessages.findIndex((message, index) => index > currentUserStart && message.author === "user");
      const answer = visibleMessages
        .slice(currentUserStart + 1, nextUserStart < 0 ? visibleMessages.length : nextUserStart)
        .findLast((message) => message.author === "agent" && !message.executionNarration && message.chunks.some((chunk) => chunk.kind === "text" && chunk.text.trim()));
      if (!answer) return;
      const navigation = {
        position: currentIndex + 1,
        total: versions.length + 1,
        onPrevious: () => setViewedVersionIndexes((prev) => ({ ...prev, [String(userIndex)]: Math.max(0, currentIndex - 1) })),
        onNext: () => setViewedVersionIndexes((prev) => {
          const next = currentIndex + 1;
          const copy = { ...prev };
          if (next >= versions.length) delete copy[String(userIndex)];
          else copy[String(userIndex)] = next;
          return copy;
        }),
      };
      map[answer.id] = navigation;
    });
    return map;
  }, [conversationVersions, userMessages, viewedVersionIndexes, visibleMessages]);

  const hasMessages = visibleMessages.length > 0;
  const hasActiveRun = thread.status === "running" || thread.status === "interrupted";
  const isStreaming = thread.status === "running" || pendingPrompts.length > 0;
  const runFailed = thread.status === "error";
  const sendError = sendMessage.error instanceof Error ? sendMessage.error.message : null;

  // 后端返回沙箱未开启错误时，弹出提示
  useEffect(() => {
    if (sendError && /沙盒|沙箱/i.test(sendError)) {
      setSandboxWarning(true);
    }
  }, [sendError]);

  const liveStatus = liveStatusFor(thread, pendingPrompts.length > 0);
  const latestUserMessage = useMemo(() => {
    for (let index = visibleMessages.length - 1; index >= 0; index -= 1) {
      if (visibleMessages[index]?.author === "user") return visibleMessages[index];
    }
    return null;
  }, [visibleMessages]);
  const handleEditMessage = async (message: Message, content: string) => {
    if (hasActiveRun || sendMessage.isPending) {
      await showDialog({
        title: "暂时无法编辑",
        message: "请先停止当前运行，再编辑消息。",
        confirmLabel: "知道了",
      });
      return;
    }
    if (!isViewingCurrentVersion || !latestUserMessage || message.id !== latestUserMessage.id) return;
    let impact = "没有需要回退的任务或代码变更。\n是否使用编辑后的内容重新发送？";
    try {
      const originalContent = messageText(message);
      const preview = await agentsApi.previewEdit(thread.id, message.id, originalContent);
      impact = preview.requires_rollback
        ? `将回退 ${preview.messages_to_replace} 条消息和 ${preview.runs_to_replace} 次任务执行。\n${preview.code_changes_detected ? "检测到代码变更，回退可能产生冲突。\n" : ""}\n请确认已了解影响后继续。`
        : "没有需要回退的任务或代码变更。\n是否使用编辑后的内容重新发送？";
    } catch (error) {
      await showDialog({
        title: "无法获取回退影响",
        message: error instanceof Error ? error.message : "请稍后重试。",
        confirmLabel: "知道了",
      });
      return;
    }
    const confirmed = await showDialog({
      title: "确认回退并重新发送",
      message: impact,
      confirmLabel: "确认并发送",
      cancelLabel: "取消",
    });
    if (!confirmed) return;
    setSendTransition(true);
    window.setTimeout(() => setSendTransition(false), 1100);
    const insertAt = Math.max(0, displayMessages.findIndex((item) => item.id === message.id));
    setPendingPrompts(addPendingPrompt(thread.id, content, insertAt, message.chunks.filter((chunk): chunk is ImageChunk => chunk.kind === "image")));
    sendMessage.mutate({
      content,
      images: message.chunks.filter((chunk): chunk is ImageChunk => chunk.kind === "image"),
      model_id: activeSelection?.modelId ?? null,
      effort: activeSelection?.effort ?? null,
      backend_type: backendType,
      project_name: thread.projectName ?? null,
      edit_message_id: message.id,
      edit_message_content: messageText(message),
      edit_snapshot: displayMessages,
      rollback_confirmed: true,
    });
  };
  const handleSubmit = (content: string, images: Array<ImageChunk>) => {
    if (!displayRepo) {
      setRepoWarning(true);
      return false;
    }
    setSendTransition(true);
    window.setTimeout(() => setSendTransition(false), 1100);
    const insertAt = displayMessages.length;
    const nextPendingPrompts = addPendingPrompt(thread.id, content, insertAt, images);
    setPendingPrompts(nextPendingPrompts);
    sendMessage.mutate({
      content,
      images,
      model_id: activeSelection?.modelId ?? null,
      effort: activeSelection?.effort ?? null,
      backend_type: backendType,
      project_name: thread.projectName ?? null,
    });
  };

  return (
    <AgentsShell user={user} activeThreadId={thread.id}>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 shrink-0 items-center justify-between border-b border-[var(--ui-border)] px-6 py-2 max-md:pl-14">
          <div className="min-w-0 flex-1">
            {editingTitle ? (
              <input
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const trimmed = draftTitle.trim();
                    if (trimmed && trimmed !== (thread.title || "新任务")) {
                      updateTitle.mutate(trimmed);
                    }
                    setEditingTitle(false);
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    setEditingTitle(false);
                  }
                }}
                onBlur={() => {
                  const trimmed = draftTitle.trim();
                  if (trimmed && trimmed !== (thread.title || "新任务")) {
                    updateTitle.mutate(trimmed);
                  }
                  setEditingTitle(false);
                }}
                className="relative z-10 block h-5 w-full max-w-md min-w-0 truncate rounded bg-[var(--ui-bg)] px-1 text-[0.9375rem] font-semibold leading-4 text-[var(--ui-text)] outline-none ring-1 ring-[var(--ui-accent)]"
                autoFocus
              />
            ) : (
              <h1
                className="block h-5 truncate text-[0.9375rem] font-semibold leading-5 text-[var(--ui-text)] cursor-default select-none"
                onDoubleClick={() => {
                  setDraftTitle(thread.title || "新任务");
                  setEditingTitle(true);
                }}
              >
                {thread.title || "新任务"}
              </h1>
            )}
            {displayRepo ? (
              <p className="relative z-0 mt-0.5 flex flex-wrap items-center gap-1.5 text-[0.6875rem] text-[color:var(--ui-text-muted)]">
                {thread.platformDisplay ? <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1 py-px text-[0.625rem] font-medium">{thread.platformDisplay}</span> : null}
                <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1 py-px text-[0.625rem] font-medium">{displayRepo}</span>
                {/* 徽章展示会话真实环境（thread.backendType），不受底部环境选择器状态影响，避免本地会话误显示为沙箱 */}
                <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1 py-px text-[0.625rem] font-medium">{thread.backendType === "sandbox" ? "沙箱" : "本地"}</span>
                {thread.backendType !== "sandbox" && thread.localWorkDir ? (
                  <span
                    className="rounded bg-[var(--ui-panel-2)] px-1 py-px text-[0.625rem] font-medium break-all"
                    title={thread.localWorkDirFull ?? thread.localWorkDir}
                  >
                    {/* 完整展示本地文件夹绝对地址，不截断 */}
                    {thread.localWorkDirFull ?? thread.localWorkDir}
                  </span>
                ) : null}
              </p>
            ) : <p className="text-[0.6875rem] text-[color:var(--ui-text-dim)]">未指定仓库</p>}
          </div>
          <span className="shrink-0 rounded-full bg-[var(--ui-accent-bubble)] px-2.5 py-1 text-[0.6875rem] text-[color:var(--ui-text-muted)]">{thread.status === "running" ? "运行中" : thread.status === "interrupted" ? "等待审批" : thread.status === "error" ? "失败" : "已完成"}</span>
        </header>
        <div className="flex min-h-0 flex-1 flex-col">
          {hasMessages ? (
            <div className={`relative flex min-h-0 flex-1 flex-col overflow-hidden ${sendTransition ? "send-transition" : ""} ${newThreadHandoff ? "new-thread-handoff" : ""}`}>
              <MessageView
                messages={visibleMessages}
                threadId={thread.id}
                isStreaming={isViewingCurrentVersion && isStreaming}
                runStatus={isViewingCurrentVersion ? thread.status : undefined}
                contentWidthClass="max-w-4xl"
                completedElapsedMs={completedElapsedMs}
                liveStatus={liveStatus}
                editableMessageId={isViewingCurrentVersion && !hasActiveRun && !isBackendMismatch ? latestUserMessage?.id : null}
                onEditMessage={handleEditMessage}
                models={models}
                selection={activeSelection}
                onSelectionChange={setSelection}
                versionNavigations={versionNavigations}
              />
              <div className="shrink-0 px-4 pb-4">
                <div className="mx-auto w-full min-w-0 max-w-4xl">
                  {runFailed ? (
                    <p className="mb-3 text-sm text-[color:var(--ui-danger)]">
                      运行失败。请查看后端控制台日志。
                    </p>
                  ) : null}
                  {sendError ? (
                    <p className="mb-3 text-sm text-[color:var(--ui-danger)]">
                      {sendError}
                    </p>
                  ) : null}
                  {!isViewingCurrentVersion && (
                    <div className="mb-3 flex items-center justify-between gap-3 border-l-2 border-[var(--ui-accent)] bg-[var(--ui-panel)] px-3 py-2 text-xs text-[color:var(--ui-text-muted)]">
                      <span>正在查看编辑前的消息版本，此视图只读。</span>
                      <button
                        type="button"
                        onClick={() => setViewedVersionIndexes({})}
                        className="shrink-0 font-medium text-[color:var(--ui-accent)] hover:underline"
                      >
                        返回当前版本
                      </button>
                    </div>
                  )}
                  {isBackendMismatch && (
                    <div className="mb-3 flex items-center gap-3 border-l-2 border-[var(--ui-accent)] bg-[var(--ui-panel)] px-3 py-2 text-xs text-[color:var(--ui-text-muted)]">
                      <span>此对话在{backendMismatchLabel}环境中创建，当前{backendType === "sandbox" ? "沙箱" : "本地"}模式下只读。切换回{backendMismatchLabel}模式后可继续对话。</span>
                    </div>
                  )}
                  {thread.pendingApproval ? (
                    <CommandApprovalDialog
                      key={thread.pendingApproval.id}
                      open
                      command={thread.pendingApproval.actions.map((action) => action.command).join("\n")}
                      reason={thread.pendingApproval.actions.map((action) => action.description).filter(Boolean).join("；")}
                      error={decideApproval.error instanceof Error ? decideApproval.error.message : null}
                      explanation={explainApproval.variables === thread.pendingApproval.id ? explainApproval.data?.explanation : null}
                      explanationError={explainApproval.variables === thread.pendingApproval.id && explainApproval.error instanceof Error ? explainApproval.error.message : null}
                      explaining={explainApproval.variables === thread.pendingApproval.id && explainApproval.isPending}
                      onExplain={() => explainApproval.mutate(thread.pendingApproval!.id)}
                      onApprove={() => decideApproval.mutate({ approvalId: thread.pendingApproval!.id, decision: "approve" })}
                      onReject={() => decideApproval.mutate({ approvalId: thread.pendingApproval!.id, decision: "reject" })}
                    />
                  ) : null}
                  <AgentPromptBar
                    placeholder={isViewingCurrentVersion ? (isBackendMismatch ? "环境不匹配，不可发送" : "继续输入指令") : "历史版本不可发送"}
                    compact
                    busy={hasActiveRun}
                    disabled={sendMessage.isPending || !isViewingCurrentVersion || isBackendMismatch}
                    readOnly={!isViewingCurrentVersion || isBackendMismatch}
                    onSubmit={handleSubmit}
                    onStop={() => cancelThread.mutate()}
                    stopping={cancelThread.isPending}
                    models={models}
                    selection={activeSelection}
                    onSelectionChange={setSelection}
                    backendType={backendType}
                    onBackendTypeChange={handleBackendTypeChange}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col px-4">
              <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-2">
              <p className="text-sm text-[var(--ui-text-dim)]">当前线程还没有消息。</p>
              {runFailed ? (
                <p className="text-sm text-[color:var(--ui-danger)]">
                  运行失败。请查看后端控制台日志。
                </p>
              ) : null}
              {sendError ? (
                <p className="text-sm text-[color:var(--ui-danger)]">
                  {sendError}
                </p>
              ) : null}
              <div className="w-full max-w-4xl">
                <AgentPromptBar
                  placeholder="发送第一条指令"
                  compact
                  disabled={sendMessage.isPending}
                  onSubmit={handleSubmit}
                  models={models}
                  selection={activeSelection}
                  onSelectionChange={setSelection}
                  backendType={backendType}
                  onBackendTypeChange={handleBackendTypeChange}
                />
              </div>
              </div>
            </div>
          )}
        </div>
      </div>
      {pageDialog && (
        <ConfirmDialog
          open
          title={pageDialog.title}
          description={pageDialog.message}
          confirmText={pageDialog.confirmLabel ?? "确定"}
          cancelText={pageDialog.cancelLabel ?? "取消"}
          showCancel={Boolean(pageDialog.cancelLabel)}
          destructive={pageDialog.title.includes("回退")}
          onOpenChange={(open) => {
            if (open) return;
            pageDialog.resolve(false);
            setPageDialog(null);
          }}
          onConfirm={() => {
            pageDialog.resolve(true);
            setPageDialog(null);
          }}
        />
      )}
      <ConfirmDialog
        open={sandboxWarning}
        title="沙箱未开启"
        description="您未开启沙盒配置，请开启后再尝试"
        confirmText="知道了"
        showCancel={false}
        onOpenChange={(open) => {
          if (!open) setSandboxWarning(false);
        }}
        onConfirm={() => setSandboxWarning(false)}
      />
      <ConfirmDialog
        open={repoWarning}
        title="未选择仓库"
        description="请先选择一个代码仓库后再发送消息"
        confirmText="知道了"
        showCancel={false}
        onOpenChange={(open) => {
          if (!open) setRepoWarning(false);
        }}
        onConfirm={() => setRepoWarning(false)}
      />
    </AgentsShell>
  );
}

