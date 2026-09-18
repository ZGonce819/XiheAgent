// @ts-nocheck - ported from open-swe-app (Electron); strict checks applied when wiring cloud APIs.
import { useRef, useEffect, useLayoutEffect, useCallback, memo, useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Copy, LoaderCircle, Pencil, ArrowUp, X } from "lucide-react";
import { diffLines } from "diff";
import { MultiFileDiff } from "@pierre/diffs/react";
import { diffOptions } from "@/components/agents/utils/diffUtils";
import { CodeBlock } from "./CodeBlock";
import { Markdown } from "./Markdown";
import { ToolExecution } from "./ToolExecution";
import { ShellCommand } from "./ShellCommand";
import { ReplyCard } from "./ReplyCard";
import { TodoList } from "./TodoList";
import type {
  AgentStatus,
  Chunk,
  Message,
  ToolExecutionChunk,
  Project,
  DiffData,
} from "@/lib/agents/types";
import type { ModelOption } from "@/lib/api";
import type { ModelSelection } from "@/lib/agents/useModelOptions";
import { formatModelSelection } from "@/lib/agents/useModelOptions";
import { groupConversationMessages } from "@/lib/agents/messageGroups";
import { cn } from "@/lib/utils";

type RenderItem =
  | { type: "text-chunk"; key: string; chunk: Chunk }
  | { type: "explored-group"; key: string; id: string; chunks: ToolExecutionChunk[] }
  | { type: "edit-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "shell-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "reply-item"; key: string; chunk: ToolExecutionChunk }
  | { type: "tool-item"; key: string; chunk: ToolExecutionChunk };

function getChunkRenderKey(chunk: Chunk, sourceIndex: number): string {
  switch (chunk.kind) {
    case "tool-execution":
      return `tool-${chunk.toolCallId}`;
    case "text":
      return `text-${sourceIndex}`;
    case "code":
      return `code-${sourceIndex}`;
    case "error":
      return `error-${sourceIndex}`;
    case "list":
      return `list-${sourceIndex}`;
    case "image":
      return `image-${sourceIndex}`;
    default:
      return `chunk-${sourceIndex}`;
  }
}

function isEditTool(chunk: ToolExecutionChunk): boolean {
  const kind = chunk.toolKind;
  if (kind === "edit" || kind === "delete" || kind === "move") return true;
  if (chunk.diffs?.length) return true;
  if (chunk.diffData) return true;
  return false;
}

function isExplorationTool(chunk: ToolExecutionChunk): boolean {
  if (chunk.diffs?.length) return false;
  if (chunk.diffData) return false;
  const kind = chunk.toolKind;
  return kind === "read" || kind === "search";
}

function isShellTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "execute";
}

function isReplyTool(chunk: ToolExecutionChunk): boolean {
  return chunk.toolKind === "slack" || chunk.toolKind === "linear";
}

function buildRenderItems(chunks: Chunk[]): RenderItem[] {
  const items: RenderItem[] = [];
  let exploredBuffer: ToolExecutionChunk[] = [];
  let exploredStartIndex = -1;

  const flushExplored = () => {
    if (exploredBuffer.length === 0) return;
    const firstId = exploredBuffer[0]?.toolCallId;
    const id = `explored-${firstId || exploredStartIndex}`;
    items.push({
      type: "explored-group",
      key: id,
      id,
      chunks: [...exploredBuffer],
    });
    exploredBuffer = [];
    exploredStartIndex = -1;
  };

  for (let i = 0; i < chunks.length; i += 1) {
    const chunk = chunks[i];

    if (chunk.kind === "tool-execution") {
      if (isExplorationTool(chunk)) {
        if (exploredBuffer.length === 0) exploredStartIndex = i;
        exploredBuffer.push(chunk);
        continue;
      }

      flushExplored();

      if (isEditTool(chunk)) {
        items.push({ type: "edit-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else if (isShellTool(chunk)) {
        items.push({ type: "shell-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else if (isReplyTool(chunk)) {
        items.push({ type: "reply-item", key: `tool-${chunk.toolCallId}`, chunk });
      } else {
        items.push({ type: "tool-item", key: `tool-${chunk.toolCallId}`, chunk });
      }
      continue;
    }

    if (chunk.kind === "text" && !chunk.text.trim()) continue;

    flushExplored();
    items.push({
      type: "text-chunk",
      key: getChunkRenderKey(chunk, i),
      chunk,
    });
  }

  flushExplored();
  return items;
}

function summarizeExploration(chunks: ToolExecutionChunk[]): string {
  const count = chunks.length;
  const paths = chunks
    .map((chunk) => chunk.input?.path as string | undefined)
    .filter((path): path is string => Boolean(path));
  const uniquePaths = [...new Set(paths)];

  if (count === 1) {
    const chunk = chunks[0];
    const path = uniquePaths[0];
    if (chunk.title.includes("目录") || chunk.toolKind === "search") {
      return path ? `已查看目录：${path}` : "已查看目录";
    }
    return path ? `已读取文件：${path}` : "已读取文件";
  }

  if (uniquePaths.length > 0) {
    const preview = uniquePaths.slice(0, 3).join("、");
    const suffix = uniquePaths.length > 3 ? ` 等 ${uniquePaths.length} 项` : "";
    return `已查看 ${count} 项：${preview}${suffix}`;
  }

  return `已查看 ${count} 项`;
}

export interface ChangedFileSummaryItem {
  filePath: string;
  additions: number;
  deletions: number;
  originalContent: string;
  modifiedContent: string;
}

function countLines(text: string): number {
  if (text.length === 0) return 0;
  const segments = text.split("\n");
  return text.endsWith("\n") ? segments.length - 1 : segments.length;
}

function countLineChanges(originalContent: string | null, newContent: string): { additions: number; deletions: number } {
  const before = originalContent ?? "";
  const parts = diffLines(before, newContent, {
    ignoreWhitespace: false,
    newlineIsToken: false,
  });

  let additions = 0;
  let deletions = 0;

  for (const part of parts) {
    const lineCount = countLines(part.value);
    if (part.added) additions += lineCount;
    else if (part.removed) deletions += lineCount;
  }

  return { additions, deletions };
}

function stripProjectPathForDisplay(path: string, projectPath?: string): string {
  if (!projectPath) return path;
  const normalizedPath = path.replace(/\\/g, "/");
  const normalizedProjectPath = projectPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalizedPath.startsWith(`${normalizedProjectPath}/`)) return path;
  return normalizedPath.slice(normalizedProjectPath.length + 1);
}

const TurnChangedFilesCard = memo(function TurnChangedFilesCard({
  files,
  totals,
  projectPath,
}: {
  files: ChangedFileSummaryItem[];
  totals: { additions: number; deletions: number };
  projectPath?: string;
}) {
  const [expandedByPath, setExpandedByPath] = useState<Record<string, boolean>>({});

  const toggleFile = useCallback((filePath: string) => {
    setExpandedByPath((prev) => ({ ...prev, [filePath]: !prev[filePath] }));
  }, []);

  return (
    <div className="mt-3 overflow-hidden rounded-xl bg-[var(--ui-accent-bubble)]">
      <div className="px-3 py-2 text-xs text-[color:var(--ui-text-muted)] border-b border-[var(--ui-border)] flex items-center gap-2">
        <span>已修改 {files.length} 个文件</span>
        <span className="text-[color:var(--ui-success)]">+{totals.additions}</span>
        <span className="text-[color:var(--ui-danger)]">-{totals.deletions}</span>
      </div>
      <div>
        {files.map((file) => {
          const displayPath = stripProjectPathForDisplay(file.filePath, projectPath);
          const open = !!expandedByPath[file.filePath];
          const oldFile = { name: displayPath, contents: file.originalContent };
          const newFile = { name: displayPath, contents: file.modifiedContent };
          return (
            <div key={file.filePath} className="border-b last:border-b-0 border-[var(--ui-border)]">
              <button
                type="button"
                onClick={() => toggleFile(file.filePath)}
                className="w-full px-3 py-2 text-left hover:bg-[var(--ui-panel-2)] transition-colors flex items-center justify-between gap-3"
                aria-expanded={open}
              >
                <span className="text-[0.8125rem] text-[color:var(--ui-accent)] truncate min-w-0">{displayPath}</span>
                <span className="shrink-0 flex items-center gap-2">
                  <span className="text-xs flex items-center gap-2">
                    <span className="text-[color:var(--ui-success)]">+{file.additions}</span>
                    <span className="text-[color:var(--ui-danger)]">-{file.deletions}</span>
                  </span>
                  {open ? (
                    <ChevronUp className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] shrink-0" aria-hidden />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] shrink-0" aria-hidden />
                  )}
                </span>
              </button>
              {open && (
                <div className="px-2 pb-2">
                  <div className="rounded-lg bg-[var(--ui-panel)] overflow-hidden border border-[var(--ui-border-subtle)]">
                    <div className="max-h-[250px] overflow-auto">
                      <MultiFileDiff
                        oldFile={oldFile}
                        newFile={newFile}
                        options={diffOptions}
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export function summarizeChangedFiles(chunks: Chunk[]): ChangedFileSummaryItem[] {
  const byFile = new Map<string, { filePath: string; originalContent: string | null; modifiedContent: string }>();

  for (const chunk of chunks) {
    if (chunk.kind !== "tool-execution") continue;
    if (chunk.status !== "completed") continue;
    const diffEntries = chunk.diffs?.length ? chunk.diffs : (chunk.diffData ? [chunk.diffData] : []);
    if (diffEntries.length === 0) continue;

    for (const diffData of diffEntries as DiffData[]) {
      const existing = byFile.get(diffData.filePath);

      if (!existing) {
        byFile.set(diffData.filePath, {
          filePath: diffData.filePath,
          originalContent: diffData.originalContent,
          modifiedContent: diffData.newContent,
        });
        continue;
      }

      byFile.set(diffData.filePath, {
        filePath: existing.filePath,
        originalContent: existing.originalContent,
        modifiedContent: diffData.newContent,
      });
    }
  }

  return [...byFile.values()]
    .map((file) => {
      const { additions, deletions } = countLineChanges(file.originalContent, file.modifiedContent);
      return {
        filePath: file.filePath,
        additions,
        deletions,
        originalContent: file.originalContent ?? "",
        modifiedContent: file.modifiedContent,
      };
    })
    .sort((a, b) => a.filePath.localeCompare(b.filePath));
}

interface ApprovalCallbacks {
  onApprove?: (approvalRequestId: string) => void;
  onReject?: (approvalRequestId: string) => void;
  onAutoApprove?: (approvalRequestId: string) => void;
  onOpenDiff?: (diffData: { filePath: string; originalContent: string; modifiedContent: string }) => void;
}

export type MessageViewScrollControl = {
  scrollToBottom: () => void;
};

interface MessageViewProps extends ApprovalCallbacks {
  messages: Message[];
  threadId?: string;
  isStreaming: boolean;
  runStatus?: AgentStatus;
  project?: Project | null;
  contentWidthClass?: string;
  /** Horizontal padding on centered content (scroll track stays edge-to-edge). */
  contentPaddingClass?: string;
  /** Extra scroll padding so content can scroll under a bottom overlay (e.g. floating prompt). */
  bottomInset?: number;
  /** Persisted duration for a completed run, recovered from run event timestamps. */
  completedElapsedMs?: number;
  /** When "external", parent renders the scroll button (e.g. above a floating prompt). */
  scrollButtonSlot?: "internal" | "external";
  onShowScrollToBottomChange?: (show: boolean) => void;
  scrollControlRef?: React.MutableRefObject<MessageViewScrollControl | null>;
  liveStatus?: string | null;
  editableMessageId?: string | null;
  onEditMessage?: (message: Message, content: string) => void;
  models?: Array<ModelOption>;
  selection?: ModelSelection | null;
  onSelectionChange?: (next: ModelSelection) => void;
  versionNavigation?: VersionNavigation;
  versionNavigations?: Record<string, VersionNavigation | undefined>;
}

interface VersionNavigation {
  position: number;
  total: number;
  onPrevious: () => void;
  onNext: () => void;
}

function formatMessageTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const BUSY_TEXTS: { present: string; past: string }[] = [
  { present: "正在启动智能体运行...", past: "智能体运行已完成" },
  { present: "正在等待智能体输出...", past: "已收到智能体输出" },
  { present: "正在读取仓库上下文...", past: "已读取仓库上下文" },
  { present: "正在规划下一步代码操作...", past: "已规划代码操作" },
  { present: "正在准备本地工作区...", past: "已准备本地工作区" },
  { present: "正在检查文件和命令...", past: "已检查文件和命令" },
];

function formatElapsed(ms: number): string {
  const secs = ms > 0 ? Math.max(1, Math.round(ms / 1000)) : 0;
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function ChunkRenderer({
  chunk,
  projectPath,
  ...callbacks
}: { chunk: Chunk; projectPath?: string } & ApprovalCallbacks) {
  switch (chunk.kind) {
    case "text":
      return (
        <div className="text-[0.9375rem] leading-7 text-[color:var(--ui-text)]">
          <Markdown content={chunk.text} />
        </div>
      );
    case "code":
      return <CodeBlock text={chunk.text} language={chunk.language} />;
    case "error":
      return <span className="text-[color:var(--ui-danger)]">{chunk.text}</span>;
    case "list":
      return (
        <div className="ml-2 text-[color:var(--ui-text-muted)]">
          {chunk.lines.map((line, i) => (
            <div key={i}>- {line}</div>
          ))}
        </div>
      );
    case "todo":
      return <TodoList todos={chunk.todos} className="my-1" />;
    case "tool-execution":
      return (
        <ToolExecution
          chunk={chunk}
          projectPath={projectPath}
          onApprove={callbacks.onApprove}
          onReject={callbacks.onReject}
          onAutoApprove={callbacks.onAutoApprove}
          onOpenDiff={callbacks.onOpenDiff}
        />
      );
    case "image":
      return (
        <img
          src={`data:${chunk.mimeType};base64,${chunk.base64}`}
          alt={chunk.fileName || "image"}
          className="max-h-48 max-w-48 rounded-md border border-[var(--ui-border)]"
        />
      );
  }
}

function VersionNavigationControls({ navigation }: { navigation?: VersionNavigation }) {
  if (!navigation || navigation.total <= 1) return null;

  return (
    <>
      <button
        type="button"
        onClick={navigation.onPrevious}
        disabled={navigation.position <= 1}
        aria-label="查看上一个版本"
        title="上一个版本"
        className="inline-flex size-6 items-center justify-center rounded-md hover:bg-[var(--ui-panel-2)] hover:text-[color:var(--ui-text)] disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronLeft className="size-3.5" />
      </button>
      <span className="min-w-8 text-center tabular-nums">
        {navigation.position}/{navigation.total}
      </span>
      <button
        type="button"
        onClick={navigation.onNext}
        disabled={navigation.position >= navigation.total}
        aria-label="查看下一个版本"
        title="下一个版本"
        className="inline-flex size-6 items-center justify-center rounded-md hover:bg-[var(--ui-panel-2)] hover:text-[color:var(--ui-text)] disabled:pointer-events-none disabled:opacity-30"
      >
        <ChevronRight className="size-3.5" />
      </button>
    </>
  );
}

function UserMessage({ message, editable = false, onEdit, models = [], selection = null, onSelectionChange }: { message: Message; editable?: boolean; onEdit?: (message: Message, content: string) => void; models?: Array<ModelOption>; selection?: ModelSelection | null; onSelectionChange?: (next: ModelSelection) => void }) {
  const text = message.chunks
    .filter((c) => c.kind === "text")
    .map((c) => (c as { kind: "text"; text: string }).text)
    .join("");

  const images = message.chunks.filter((c) => c.kind === "image");
  const textRef = useRef<HTMLDivElement>(null);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [editModelDropdownOpen, setEditModelDropdownOpen] = useState(false);
  const editModelDropdownRef = useRef<HTMLDivElement>(null);

  const updateScrollIndicators = useCallback(() => {
    const el = textRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  useLayoutEffect(() => {
    updateScrollIndicators();
  }, [text, updateScrollIndicators]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      if (editModelDropdownRef.current && !editModelDropdownRef.current.contains(target)) {
        setEditModelDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const textEdgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className={`group my-5 flex w-full justify-end ${message.id.startsWith("pending-user-") ? "pending-user-pop" : "message-enter"}`}>
      <div className="flex w-full min-w-0 flex-col items-end">
        {images.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap justify-end">
            {images.map((img, i) => (
              img.kind === "image" && (
                <img
                  key={i}
                  src={`data:${img.mimeType};base64,${img.base64}`}
                  alt={img.fileName || "image"}
                  className="max-h-48 max-w-48 rounded-md border border-[var(--ui-border)]"
                />
              )
            ))}
          </div>
        )}
        {editing ? (
          <div className="w-[min(78vw,720px)] rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] p-3 shadow-sm">
            <textarea autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} className="min-h-24 w-full resize-none bg-transparent text-[0.875rem] leading-6 text-[color:var(--ui-text)] outline-none" />
            <div className="mt-2 flex items-center justify-between gap-2">
              <div ref={editModelDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => setEditModelDropdownOpen((open) => !open)}
                  className="flex max-w-[220px] cursor-pointer items-center gap-0.5 rounded-md px-1 py-1 text-[0.8125rem] text-[color:var(--ui-text-muted)] transition-[color,opacity] duration-150 hover:opacity-80"
                >
                  <span className="truncate">{formatModelSelection(models, selection)}</span>
                  <ChevronDown className={cn("size-3.5 shrink-0 opacity-60 transition-transform duration-200 ease-out", editModelDropdownOpen && "rotate-180")} />
                </button>
                {editModelDropdownOpen && (
                  <div className="absolute bottom-full left-0 z-50 mb-1 min-w-[160px] origin-bottom-left animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-1 duration-150 ease-out overflow-hidden overflow-y-auto rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] text-xs shadow-lg">
                    {models.flatMap((model) =>
                      model.efforts.map((effort) => {
                        const combo: ModelSelection = { modelId: model.id, effort };
                        const isSelected = !!selection && selection.modelId === model.id && selection.effort === effort;
                        return (
                          <button
                            key={model.id + "::" + effort}
                            type="button"
                            onClick={() => {
                              onSelectionChange?.(combo);
                              setEditModelDropdownOpen(false);
                            }}
                            className={cn(
                              "flex w-full items-center gap-2 px-3 py-1.5 text-left whitespace-nowrap transition-colors hover:bg-[var(--ui-panel-2)]",
                              isSelected ? "text-[color:var(--ui-text)]" : "text-[color:var(--ui-text-muted)]"
                            )}
                          >
                            {formatModelSelection(models, combo)}
                            {isSelected && <span className="ml-auto text-[color:var(--ui-text-dim)]">✓</span>}
                          </button>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
              <button type="button" onClick={() => setEditing(false)} className="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-[color:var(--ui-text-muted)] hover:bg-[var(--ui-panel-2)]"><X className="size-3.5" />取消</button>
              <button type="button" disabled={!draft.trim()} onClick={() => { if (draft.trim()) { onEdit?.(message, draft.trim()); setEditing(false); } }} className="inline-flex items-center gap-1 rounded-md bg-[var(--ui-accent)] px-2.5 py-1 text-xs text-white disabled:opacity-40"><ArrowUp className="size-3.5" />发送</button>
              </div>
            </div>
          </div>
        ) : text && (
          <div className="w-fit max-w-[min(74%,52rem)] self-end overflow-hidden rounded-xl bg-[var(--ui-accent-bubble)]">
            <div
              ref={textRef}
              onScroll={updateScrollIndicators}
              className="max-h-[250px] overflow-auto whitespace-pre-wrap px-4 py-3 text-[0.9375rem] leading-7 text-[color:var(--ui-text)] break-words"
              style={{ boxShadow: textEdgeShadows || "none" }}
            >
              {text}
            </div>
          </div>
        )}
        <div className="mt-1.5 flex items-center justify-end gap-1 pr-1 text-[0.6875rem] text-[color:var(--ui-text-dim)] opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100">
          <time dateTime={message.timestamp} className="px-1">{formatMessageTimestamp(message.timestamp)}</time>
          <button type="button" onClick={() => void navigator.clipboard?.writeText(text)} aria-label="复制消息" title="复制" className="inline-flex size-6 items-center justify-center rounded-md hover:bg-[var(--ui-panel-2)]"><Copy className="size-3.5" /></button>
          {editable && <button type="button" onClick={() => { setDraft(text); setEditing(true); }} aria-label="编辑并重新发送" title="编辑并重新发送" className="inline-flex size-6 items-center justify-center rounded-md hover:bg-[var(--ui-panel-2)]"><Pencil className="size-3.5" /></button>}
        </div>
      </div>
    </div>
  );
}

function AgentMessageActions({ message, versionNavigation }: { message: Message; versionNavigation?: VersionNavigation }) {
  const body = message.chunks
    .filter((chunk): chunk is Extract<Chunk, { kind: "text" }> => chunk.kind === "text")
    .map((chunk) => chunk.text)
    .join("");
  if (!body.trim()) return null;

  return (
    <div className="mt-1 flex min-h-7 items-center gap-1 text-[0.6875rem] text-[color:var(--ui-text-dim)] opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
      <button
        type="button"
        onClick={() => void navigator.clipboard?.writeText(body)}
        aria-label="复制智能体正文"
        title="复制正文"
        className="inline-flex size-6 items-center justify-center rounded-md hover:bg-[var(--ui-panel-2)] hover:text-[color:var(--ui-text)]"
      >
        <Copy className="size-3.5" />
      </button>
      <time dateTime={message.timestamp} className="px-1">
        {formatMessageTimestamp(message.timestamp)}
      </time>
      <VersionNavigationControls navigation={versionNavigation} />
    </div>
  );
}

function ToolActivityGroup({
  message,
  isStreaming,
  runStatus,
  runElapsedMs,
  showTodos = true,
  hasModelContent = false,
  projectPath,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  runStatus?: AgentStatus;
  runElapsedMs?: number;
  showTodos?: boolean;
  hasModelContent?: boolean;
  projectPath?: string;
} & ApprovalCallbacks) {
  const tools = message.chunks.filter(
    (chunk): chunk is ToolExecutionChunk => chunk.kind === "tool-execution",
  );
  const running = Boolean(isStreaming);
  const completedCount = tools.filter((tool) => tool.status === "completed").length;
  const errorCount = tools.filter((tool) => tool.status === "error").length;
  const toolElapsedMs = tools.reduce((total, tool) => total + (tool.elapsedMs ?? 0), 0);
  const elapsedMs = runElapsedMs && runElapsedMs > 0 ? runElapsedMs : toolElapsedMs;
  const [expanded, setExpanded] = useState(Boolean(isStreaming || running));
  const wasStreaming = useRef(isStreaming);
  useEffect(() => {
    if (wasStreaming.current && !isStreaming) setExpanded(false);
    wasStreaming.current = isStreaming;
  }, [isStreaming]);

  const activeTool = [...tools].reverse().find(
    (tool) => tool.status === "pending" || tool.status === "in_progress",
  );
  const summary = activeTool?.title || "执行任务";
  const duration = elapsedMs > 0 ? formatElapsed(elapsedMs) : (isStreaming ? "" : "1s");
  const statusText = running
    ? "正在运行"
    : runStatus === "interrupted"
      ? "等待确认后继续"
      : runStatus === "error"
        ? "任务失败"
        : runStatus === "finished"
          ? `任务完成 · 耗时 ${duration || "0s"}`
          : errorCount > 0
            ? `任务失败 · ${errorCount} 项失败`
            : ((tools.length === 0 && hasModelContent) || (tools.length > 0 && completedCount === tools.length))
              ? `任务完成 · 耗时 ${duration || "0s"}`
              : "任务已停止";

  return (
    <div className="my-3 overflow-hidden border-b border-[var(--ui-border)]" data-task-execution>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="flex min-h-9 w-full items-center justify-between gap-3 px-0 py-1 text-left transition-[color,background-color] duration-150 hover:text-[color:var(--ui-text)]"
        aria-expanded={expanded}
      >
        <span className="min-w-0 truncate text-[0.8125rem] text-[color:var(--ui-text-muted)]">
          {statusText}
          {running && summary ? ` · ${summary}` : ""}
        </span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-[color:var(--ui-text-dim)] transition-transform duration-200 ease-out",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded && (
        <div role="region" aria-label="任务执行详情" className="max-h-64 animate-in fade-in-0 slide-in-from-top-1 duration-150 ease-out overflow-y-auto px-0 pb-2 pt-1">
          {tools.length === 0 ? (
            <p className="py-1 text-xs text-[color:var(--ui-text-dim)]">本次任务无工具调用</p>
          ) : null}
          {message.chunks.map((chunk, index) => {
            if (chunk.kind === "todo" && !showTodos) return null;
            return (
              <div key={getChunkRenderKey(chunk, index)} className="mb-2">
                <ChunkRenderer chunk={chunk} projectPath={projectPath} {...callbacks} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AgentMessage({
  message,
  isStreaming,
  runStatus,
  runElapsedMs,
  projectPath,
  versionNavigation,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  runStatus?: AgentStatus;
  runElapsedMs?: number;
  projectPath?: string;
  versionNavigation?: VersionNavigation;
} & ApprovalCallbacks) {
  const renderItems = useMemo(() => buildRenderItems(message.chunks), [message.chunks]);
  const changedFiles = useMemo(() => summarizeChangedFiles(message.chunks), [message.chunks]);
  const changedFilesTotals = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const item of changedFiles) {
      additions += item.additions;
      deletions += item.deletions;
    }
    return { additions, deletions };
  }, [changedFiles]);
  const changedFilesByPath = useMemo(() => {
    const byPath = new Map<string, ChangedFileSummaryItem>();
    for (const file of changedFiles) {
      byPath.set(file.filePath, file);
    }
    return byPath;
  }, [changedFiles]);

  const exploredGroupIds = useMemo(
    () =>
      renderItems
        .filter((item): item is Extract<RenderItem, { type: "explored-group" }> => item.type === "explored-group")
        .map((item) => item.id),
    [renderItems],
  );
  const hasExploredGroups = exploredGroupIds.length > 0;
  const [expandedExploredGroups, setExpandedExploredGroups] = useState<Record<string, boolean>>({});
  const wasExplorationLiveRef = useRef(false);

  useEffect(() => {
    const isLive = !!isStreaming;

    if (!hasExploredGroups) {
      setExpandedExploredGroups({});
      wasExplorationLiveRef.current = isLive;
      return;
    }

    if (isLive) {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) next[id] = true;
      setExpandedExploredGroups(next);
      wasExplorationLiveRef.current = true;
      return;
    }

    const shouldAutoCollapse = wasExplorationLiveRef.current;
    setExpandedExploredGroups((prev) => {
      const next: Record<string, boolean> = {};
      for (const id of exploredGroupIds) {
        next[id] = shouldAutoCollapse ? false : (prev[id] ?? false);
      }
      return next;
    });
    wasExplorationLiveRef.current = false;
  }, [hasExploredGroups, isStreaming, exploredGroupIds, message.id]);

  const activity = message;
  const hasToolActivity = activity.executionNarration
    || activity.chunks.some((chunk) => chunk.kind === "tool-execution");
  if (hasToolActivity) {
    const hasTools = activity.chunks.some((chunk) => chunk.kind === "tool-execution");
    const panelActivity = hasTools
      ? {
          ...activity,
          chunks: activity.chunks.filter((chunk) =>
            chunk.kind === "tool-execution"
            || (chunk.kind === "todo" && !isStreaming)
            || (chunk.kind === "text" && (!isStreaming || !activity.chunks.some((item) => item.kind === "todo"))),
          ),
        }
      : { ...activity, chunks: activity.chunks.filter((chunk) => chunk.kind === "todo") };
    const remainingChunks = hasTools
      ? (isStreaming && activity.chunks.some((chunk) => chunk.kind === "todo")
          ? activity.chunks.filter((chunk) => chunk.kind === "text" || chunk.kind === "todo")
          : [])
      : activity.chunks.filter((chunk) => chunk.kind !== "tool-execution" && chunk.kind !== "todo");
    const answerMessage = remainingChunks.length > 0
      ? { ...message, chunks: remainingChunks }
      : null;
    return (
      <div className="group message-enter my-2 min-w-0 space-y-2">
        <ToolActivityGroup
          message={panelActivity}
          isStreaming={isStreaming}
          runStatus={runStatus}
          runElapsedMs={runElapsedMs}
          showTodos={!isStreaming}
          hasModelContent={!hasTools && activity.chunks.some((chunk) => chunk.kind !== "todo")}
          projectPath={projectPath}
          {...callbacks}
        />
        {remainingChunks.map((chunk, index) => (
          <ChunkRenderer
            key={getChunkRenderKey(chunk, index)}
            chunk={chunk}
            projectPath={projectPath}
            {...callbacks}
          />
        ))}
        {answerMessage ? (
          <AgentMessageActions message={answerMessage} versionNavigation={versionNavigation} />
        ) : null}
      </div>
    );
  }

  return (
    <div className="group message-enter my-2 min-w-0 space-y-2">
      {renderItems.map((item) => {
        switch (item.type) {
          case "explored-group": {
            const summary = summarizeExploration(item.chunks);
            const isExpanded = expandedExploredGroups[item.id] ?? false;
            return (
              <div key={item.key}>
                <button
                  type="button"
                  onClick={() =>
                    setExpandedExploredGroups((prev) => ({
                      ...prev,
                      [item.id]: !(prev[item.id] ?? false),
                    }))
                  }
                  className="w-full flex items-center justify-between py-1 text-left hover:opacity-90 transition-opacity"
                >
                  <span className="text-[color:var(--ui-text-muted)] text-[0.75rem]">{summary}</span>
                  <span className="text-[color:var(--ui-text-dim)] text-xs">{isExpanded ? "隐藏" : "显示"}</span>
                </button>
                {isExpanded && (
                  <div className="pt-1 pb-1 space-y-0.5">
                    {item.chunks.map((chunk, chunkIndex) => (
                      <div key={chunk.toolCallId || `explored-chunk-${item.id}-${chunkIndex}`} className="min-w-0 flex-1 text-[color:var(--ui-text-muted)]">
                        <ToolExecution
                          chunk={chunk}
                          projectPath={projectPath}
                          onOpenDiff={callbacks.onOpenDiff}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          }

          case "edit-item": {
            const fullFileDiff = item.chunk.diffData
              ? changedFilesByPath.get(item.chunk.diffData.filePath)
              : undefined;

            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                  resolvedDiffData={fullFileDiff ? {
                    originalContent: fullFileDiff.originalContent,
                    modifiedContent: fullFileDiff.modifiedContent,
                  } : undefined}
                />
              </div>
            );
          }

          case "shell-item":
            return (
              <div key={item.key}>
                <ShellCommand
                  chunk={item.chunk}
                  projectPath={projectPath}
                />
              </div>
            );

          case "reply-item":
            return (
              <div key={item.key}>
                <ReplyCard chunk={item.chunk} />
              </div>
            );

          case "tool-item":
            return (
              <div key={item.key}>
                <ToolExecution
                  chunk={item.chunk}
                  projectPath={projectPath}
                  onApprove={callbacks.onApprove}
                  onReject={callbacks.onReject}
                  onAutoApprove={callbacks.onAutoApprove}
                  onOpenDiff={callbacks.onOpenDiff}
                />
              </div>
            );

          case "text-chunk":
            return (
              <div key={item.key} className="flex-1 min-w-0">
                <ChunkRenderer
                  chunk={item.chunk}
                  projectPath={projectPath}
                  {...callbacks}
                />
              </div>
            );
        }
      })}

      {changedFiles.length > 0 && !isStreaming && (
        <TurnChangedFilesCard
          files={changedFiles}
          totals={changedFilesTotals}
          projectPath={projectPath}
        />
      )}
      <AgentMessageActions message={message} versionNavigation={versionNavigation} />
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({
  message,
  isStreaming,
  runStatus,
  runElapsedMs,
  projectPath,
  editable,
  onEdit,
  models,
  selection,
  onSelectionChange,
  versionNavigation,
  ...callbacks
}: {
  message: Message;
  isStreaming?: boolean;
  runStatus?: AgentStatus;
  runElapsedMs?: number;
  projectPath?: string;
  editable?: boolean;
  onEdit?: (message: Message, content: string) => void;
  models?: Array<ModelOption>;
  selection?: ModelSelection | null;
  onSelectionChange?: (next: ModelSelection) => void;
  versionNavigation?: VersionNavigation;
} & ApprovalCallbacks) {
  if (message.author === "user") {
    return <UserMessage message={message} editable={editable} onEdit={onEdit} models={models} selection={selection} onSelectionChange={onSelectionChange} />;
  }
  return (
    <AgentMessage
      message={message}
      isStreaming={isStreaming}
      runStatus={runStatus}
      runElapsedMs={runElapsedMs}
      projectPath={projectPath}
      versionNavigation={versionNavigation}
      {...callbacks}
    />
  );
});

function ThinkingSpinner({
  isStreaming,
  liveStatus,
  runElapsedMs,
  runStatus,
}: {
  isStreaming: boolean;
  liveStatus?: string | null;
  runElapsedMs?: number;
  runStatus?: string;
}) {
  const [textIdx, setTextIdx] = useState(0);
  const [done, setDone] = useState<{ past: string; elapsed: string } | null>(null);
  const startTimeRef = useRef(0);
  const textIdxRef = useRef(textIdx);
  const wasStreamingRef = useRef(false);
  textIdxRef.current = textIdx;

  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true;
      startTimeRef.current = Date.now();
      setTextIdx(Math.floor(Math.random() * BUSY_TEXTS.length));
      setDone(null);
    } else if (wasStreamingRef.current) {
      // 运行失败时不展示“已收到智能体输出”，由外层错误提示负责
      if (runStatus === "error") {
        setDone(null);
      } else {
        setDone({
          past: BUSY_TEXTS[textIdxRef.current].past,
          elapsed: formatElapsed(runElapsedMs ?? (Date.now() - startTimeRef.current)),
        });
      }
    }
  }, [isStreaming, runElapsedMs, runStatus]);

  useEffect(() => {
    if (!isStreaming) return;
    const BUSY_TEXT_ROTATE_INTERVAL_MS = 12000;
    const id = setInterval(() => setTextIdx((i) => (i + 1) % BUSY_TEXTS.length), BUSY_TEXT_ROTATE_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isStreaming]);

  if (!isStreaming && !done) return null;

  if (done) {
    return (
      <div className="agents-thinking-status my-2 flex items-center gap-2">
        <CheckCircle2 className="size-3.5 text-[color:var(--ui-success)]" />
        <span className="text-xs text-[color:var(--ui-text-dim)]">{done.past}，耗时 {done.elapsed}</span>
      </div>
    );
  }

  return (
    <div className="agents-thinking-status my-2 flex items-center gap-2">
      <LoaderCircle className="agents-spinner size-3.5 text-[color:var(--ui-accent)]" />
      <span className="text-xs text-[color:var(--ui-text-muted)]">{liveStatus || BUSY_TEXTS[textIdx].present}</span>
    </div>
  );
}

const BOTTOM_LOCK_THRESHOLD_PX = 24;

export const MessageView = memo(function MessageView({
  messages,
  threadId,
  isStreaming,
  runStatus,
  project,
  contentWidthClass = "max-w-[42rem]",
  contentPaddingClass = "px-6",
  bottomInset = 0,
  completedElapsedMs = 0,
  scrollButtonSlot = "internal",
  onShowScrollToBottomChange,
  scrollControlRef,
  liveStatus,
  onApprove,
  onReject,
  onAutoApprove,
  onOpenDiff,
  editableMessageId,
  onEditMessage,
  models,
  selection,
  onSelectionChange,
  versionNavigation,
  versionNavigations,
}: MessageViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const autoScrollEnabledRef = useRef(true);
  const lastManualScrollTopRef = useRef(0);
  const previousScrollTopRef = useRef(0);
  const previousScrollHeightRef = useRef(0);
  const pendingScrollFrameRef = useRef<number | null>(null);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const streamElapsedStorageKey = threadId ? `xihe:run-elapsed:${threadId}` : null;
  const [streamElapsedMs, setStreamElapsedMs] = useState(() => {
    if (!streamElapsedStorageKey || typeof window === "undefined") return 0;
    try {
      const stored = Number(window.sessionStorage.getItem(streamElapsedStorageKey));
      return Number.isFinite(stored) && stored > 0 ? stored : 0;
    } catch {
      return 0;
    }
  });
  const streamStartedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      if (streamStartedAtRef.current !== null) {
        const elapsed = Math.max(0, Date.now() - streamStartedAtRef.current);
        setStreamElapsedMs(elapsed);
        if (streamElapsedStorageKey && typeof window !== "undefined") {
          try {
            window.sessionStorage.setItem(streamElapsedStorageKey, String(elapsed));
          } catch {
            // Storage can be unavailable in private browsing or embedded webviews.
          }
        }
        streamStartedAtRef.current = null;
      }
      return;
    }

    if (streamStartedAtRef.current === null) {
      streamStartedAtRef.current = Date.now();
      setStreamElapsedMs(0);
      if (streamElapsedStorageKey && typeof window !== "undefined") {
        try {
          window.sessionStorage.removeItem(streamElapsedStorageKey);
        } catch {
          // Storage can be unavailable in private browsing or embedded webviews.
        }
      }
    }
    const timer = window.setInterval(() => {
      if (streamStartedAtRef.current !== null) {
        setStreamElapsedMs(Math.max(0, Date.now() - streamStartedAtRef.current));
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, [isStreaming, streamElapsedStorageKey]);

  const clearScheduledScroll = useCallback(() => {
    if (pendingScrollFrameRef.current === null) return;
    window.cancelAnimationFrame(pendingScrollFrameRef.current);
    pendingScrollFrameRef.current = null;
  }, []);

  const isNearBottom = useCallback((el: HTMLDivElement) => {
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distanceFromBottom <= BOTTOM_LOCK_THRESHOLD_PX;
  }, []);

  const syncScrollButtonVisibility = useCallback((el: HTMLDivElement) => {
    setShowScrollToBottom(!isNearBottom(el));
  }, [isNearBottom]);

  const scrollToBottomNow = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;

    el.scrollTop = el.scrollHeight;
    const currentTop = el.scrollTop;
    lastManualScrollTopRef.current = currentTop;
    previousScrollTopRef.current = currentTop;
    previousScrollHeightRef.current = el.scrollHeight;
    syncScrollButtonVisibility(el);
  }, [syncScrollButtonVisibility]);

  const scheduleScrollToBottom = useCallback(() => {
    if (!autoScrollEnabledRef.current) return;

    clearScheduledScroll();
    pendingScrollFrameRef.current = window.requestAnimationFrame(() => {
      pendingScrollFrameRef.current = null;
      if (!autoScrollEnabledRef.current) return;
      scrollToBottomNow();
    });
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleScroll = () => {
      const currentTop = el.scrollTop;
      const scrolledUp = currentTop < previousScrollTopRef.current - 1;
      const nearBottom = isNearBottom(el);

      if (scrolledUp) {
        autoScrollEnabledRef.current = false;
        clearScheduledScroll();
      } else if (nearBottom) {
        autoScrollEnabledRef.current = true;
      }

      syncScrollButtonVisibility(el);
      lastManualScrollTopRef.current = currentTop;
      previousScrollTopRef.current = currentTop;
    };

    // 只在组件第一次接管滚动容器时初始化底部位置。
    // 后续消息更新由下面的 layout effect 按“贴底跟随 / 保持当前位置”规则处理，
    // 避免 SSE 增量更新或 pending 消息去重时把视口突然拉回会话顶部。
    if (previousScrollHeightRef.current === 0) {
      scrollToBottomNow();
    }
    autoScrollEnabledRef.current = true;

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", handleScroll);
      clearScheduledScroll();
    };
  }, [clearScheduledScroll, isNearBottom, scrollToBottomNow, syncScrollButtonVisibility]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const previousScrollHeight = previousScrollHeightRef.current || el.scrollHeight;

    if (autoScrollEnabledRef.current) {
      previousScrollHeightRef.current = el.scrollHeight;
      scheduleScrollToBottom();
      return;
    }

    const maxTop = Math.max(0, el.scrollHeight - el.clientHeight);
    const targetTop = Math.min(lastManualScrollTopRef.current, maxTop);
    const jumpDistance = Math.abs(el.scrollTop - targetTop);

    if (jumpDistance > 1) {
      el.scrollTop = targetTop;
    }

    previousScrollTopRef.current = el.scrollTop;
    lastManualScrollTopRef.current = el.scrollTop;
    previousScrollHeightRef.current = el.scrollHeight;
    syncScrollButtonVisibility(el);
  }, [messages, isStreaming, scheduleScrollToBottom, syncScrollButtonVisibility]);

  useEffect(() => {
    const scroller = scrollRef.current;
    const content = contentRef.current;
    if (!scroller || !content || typeof ResizeObserver === "undefined") return;

    const resizeObserver = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) {
        previousScrollHeightRef.current = scroller.scrollHeight;
        scheduleScrollToBottom();
        return;
      }

      const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      if (lastManualScrollTopRef.current > maxTop) {
        scroller.scrollTop = maxTop;
        lastManualScrollTopRef.current = maxTop;
        previousScrollTopRef.current = maxTop;
      }

      previousScrollHeightRef.current = scroller.scrollHeight;
      syncScrollButtonVisibility(scroller);
    });

    resizeObserver.observe(scroller);
    resizeObserver.observe(content);

    return () => resizeObserver.disconnect();
  }, [scheduleScrollToBottom, syncScrollButtonVisibility]);

  const visibleMessages = useMemo(() => groupConversationMessages(messages), [messages]);
  const completedConversationElapsedMs = useMemo(() => {
    if (completedElapsedMs > 0) return completedElapsedMs;
    if (isStreaming) return 0;
    const user = [...visibleMessages].reverse().find((message) => message.author === "user");
    const agent = [...visibleMessages].reverse().find((message) => message.author === "agent");
    if (!user || !agent) return 0;
    const start = Date.parse(user.timestamp);
    const end = Date.parse(agent.timestamp);
    return Number.isFinite(start) && Number.isFinite(end) && end > start ? end - start : 0;
  }, [completedElapsedMs, isStreaming, visibleMessages]);
  const currentTurnStart = visibleMessages.findLastIndex((message) => message.author === "user");
  const latestAgentMessageId = useMemo(
    () =>
      [...visibleMessages]
        .reverse()
        .find(
          (message) =>
            message.author === "agent"
            && message.chunks.some((chunk) => chunk.kind === "text" && chunk.text.trim())
            && (!message.executionNarration
              || !message.chunks.some((chunk) => chunk.kind === "tool-execution")),
        )?.id ?? null,
    [visibleMessages],
  );
  const versionAnchorStorageKey = threadId ? `xihe:version-anchor:${threadId}` : null;
  const versionAnchorIdRef = useRef<string | null>(null);
  const versionAnchorOwnerRef = useRef<string | null>(null);
  if (versionAnchorOwnerRef.current !== versionAnchorStorageKey) {
    versionAnchorOwnerRef.current = versionAnchorStorageKey;
    if (versionAnchorStorageKey && typeof window !== "undefined") {
      try {
        versionAnchorIdRef.current = window.sessionStorage.getItem(versionAnchorStorageKey);
      } catch {
        versionAnchorIdRef.current = null;
      }
    } else {
      versionAnchorIdRef.current = null;
    }
  }
  const versionAnchorId = visibleMessages.some((message) => message.id === versionAnchorIdRef.current)
    ? versionAnchorIdRef.current
    : latestAgentMessageId;
  if (versionAnchorIdRef.current !== versionAnchorId) {
    versionAnchorIdRef.current = versionAnchorId;
    if (versionAnchorStorageKey && typeof window !== "undefined") {
      try {
        if (versionAnchorId) window.sessionStorage.setItem(versionAnchorStorageKey, versionAnchorId);
        else window.sessionStorage.removeItem(versionAnchorStorageKey);
      } catch {
        // Storage can be unavailable in private browsing or embedded webviews.
      }
    }
  }
  const handleScrollToBottom = useCallback(() => {
    autoScrollEnabledRef.current = true;
    clearScheduledScroll();
    scrollToBottomNow();
  }, [clearScheduledScroll, scrollToBottomNow]);

  useEffect(() => {
    if (!scrollControlRef) return;
    scrollControlRef.current = { scrollToBottom: handleScrollToBottom };
    return () => {
      scrollControlRef.current = null;
    };
  }, [handleScrollToBottom, scrollControlRef]);

  useEffect(() => {
    onShowScrollToBottomChange?.(showScrollToBottom);
  }, [onShowScrollToBottomChange, showScrollToBottom]);

  return (
    <div className="relative flex-1 min-h-0 min-w-0">
      <div
        ref={scrollRef}
        className="h-full min-h-0 min-w-0 overflow-y-auto overflow-x-hidden py-6 text-[0.875rem] leading-6 font-sans antialiased [overflow-anchor:none]"
      >
        <div
          ref={contentRef}
          className={`w-full ${contentWidthClass} mx-auto min-w-0 ${contentPaddingClass}`}
          style={bottomInset > 0 ? { paddingBottom: bottomInset } : undefined}
        >
          {visibleMessages.map((message, index) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  isStreaming={isStreaming && index > currentTurnStart}
                  runStatus={index > currentTurnStart ? runStatus : undefined}
                  runElapsedMs={
                    index > currentTurnStart && (isStreaming || message.executionNarration)
                      ? (streamElapsedMs || completedConversationElapsedMs)
                      : undefined
                  }
                  projectPath={project?.path}
                  onApprove={onApprove}
                  onReject={onReject}
                  onAutoApprove={onAutoApprove}
                  onOpenDiff={onOpenDiff}
                  editable={message.id === editableMessageId && !isStreaming}
                  onEdit={onEditMessage}
                  models={models}
                  selection={selection}
                  onSelectionChange={onSelectionChange}
                  versionNavigation={versionNavigations?.[message.id] ?? (message.id === versionAnchorId ? versionNavigation : undefined)}
                />
          ))}
          <ThinkingSpinner isStreaming={isStreaming} liveStatus={liveStatus} runElapsedMs={streamElapsedMs || completedConversationElapsedMs} runStatus={runStatus} />
        </div>
      </div>

      {scrollButtonSlot === "internal" && showScrollToBottom && (
        <button
          type="button"
          onClick={handleScrollToBottom}
          aria-label="滚动到底部"
          className="absolute left-1/2 z-30 inline-flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full bg-[var(--ui-panel-2)] text-[color:var(--ui-text-muted)] shadow-[0_2px_8px_rgb(30_45_70_/_14%)] transition-colors hover:bg-[var(--ui-panel)] hover:text-[color:var(--ui-text)]"
          style={{ bottom: bottomInset > 0 ? bottomInset + 8 : 16 }}
        >
          {isStreaming && <span className="agents-scroll-loader" aria-hidden />}
          <ChevronDown className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
});
