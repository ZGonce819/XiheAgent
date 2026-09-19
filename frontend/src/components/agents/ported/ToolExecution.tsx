import { memo, useState, useCallback, useRef, useLayoutEffect } from "react";
import { ChevronDown } from "lucide-react";
import { diffLines } from "diff";
import { MultiFileDiff } from "@pierre/diffs/react";
import type { ToolExecutionChunk, AcpToolKind } from "@/lib/agents/types";
import { DiffView } from "./DiffView";
import { diffOptions } from "@/components/agents/utils/diffUtils";

interface ToolExecutionProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
  onApprove?: (approvalRequestId: string) => void;
  onReject?: (approvalRequestId: string) => void;
  onAutoApprove?: (approvalRequestId: string) => void;
  onOpenDiff?: (diffData: { filePath: string; originalContent: string; modifiedContent: string }) => void;
  resolvedDiffData?: { originalContent: string; modifiedContent: string };
}

function stripProjectPath(path: string, projectPath?: string): string {
  const normalizedPath = path.replace(/\\/g, "/");
  if (!projectPath) return normalizedPath;
  const normalizedProject = projectPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (normalizedPath === normalizedProject) return ".";
  if (!normalizedPath.startsWith(`${normalizedProject}/`)) return normalizedPath;
  return normalizedPath.slice(normalizedProject.length + 1);
}

function getFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

function countLines(text: string): number {
  if (text.length === 0) return 0;
  const segments = text.split("\n");
  return text.endsWith("\n") ? segments.length - 1 : segments.length;
}

function countLineChanges(originalContent: string | null | undefined, newContent: string): { additions: number; deletions: number } {
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

function formatToolDisplay(
  title: string,
  toolKind: AcpToolKind,
  input: Record<string, unknown> | undefined,
  projectPath?: string,
): string {
  const path = input?.path as string | undefined;
  const pattern = input?.pattern as string | undefined;
  const query = input?.query as string | undefined;
  const url = input?.url as string | undefined;
  const command = input?.command as string | undefined;

  switch (toolKind) {
    case "read": {
      if (path) {
        return `读取文件(${stripProjectPath(path, projectPath)})`;
      }
      return "读取文件";
    }
    case "search": {
      if (path) {
        const displayPath = stripProjectPath(path, projectPath);
        return `查看目录(${displayPath})`;
      }
      if (pattern) {
        const truncated = pattern.length > 40 ? pattern.slice(0, 40) + "..." : pattern;
        return `搜索("${truncated}")`;
      }
      if (query) {
        return `搜索("${query.slice(0, 40)}${query.length > 40 ? "..." : ""}")`;
      }
      return title;
    }
    case "fetch": {
      if (url) {
        return `获取(${url.slice(0, 50)}${url.length > 50 ? "..." : ""})`;
      }
      return title;
    }
    case "execute": {
      if (command) {
        const truncated = command.length > 60 ? command.slice(0, 60) + "..." : command;
        return `终端(${truncated})`;
      }
      return title;
    }
    case "edit":
    case "delete":
    case "move":
      return title;
    case "think":
      return title;
    default:
      return title;
  }
}

const inlineDiffOptions = {
  ...diffOptions,
  disableFileHeader: true,
};

const InlineDiffCollapsible = memo(function InlineDiffCollapsible({
  filePath,
  fileName,
  originalContent,
  newContent,
  additions,
  deletions,
  isError,
}: {
  filePath: string;
  fileName: string;
  originalContent: string;
  newContent: string;
  additions: number;
  deletions: number;
  isError: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const toggle = useCallback(() => setExpanded((prev) => !prev), []);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(false);

  const updateScrollIndicators = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  useLayoutEffect(() => {
    if (expanded) updateScrollIndicators();
  }, [expanded, updateScrollIndicators]);

  const edgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  const oldFile = { name: filePath, contents: originalContent };
  const newFile = { name: filePath, contents: newContent };

  if (!expanded) {
    return (
      <div className="my-0.5 text-[0.75rem] leading-5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-1.5 text-left hover:brightness-125 transition-colors"
        >
          <span className={isError ? "text-[color:var(--ui-danger)]" : "text-[color:var(--ui-text-muted)]"}>
            已编辑 <span className="text-[color:var(--ui-accent)]">{fileName}</span>
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="my-1">
      <div className="my-0.5 text-[0.75rem] leading-5 mb-1.5">
        <button
          type="button"
          onClick={toggle}
          className="inline-flex items-center gap-1.5 text-left hover:brightness-125 transition-colors"
        >
          <span className={isError ? "text-[color:var(--ui-danger)]" : "text-[color:var(--ui-text-muted)]"}>
            已编辑文件
          </span>
          <span className="text-[color:var(--ui-text-dim)] text-[0.625rem]">▾</span>
        </button>
      </div>

      <div className="rounded-lg bg-[var(--ui-accent-bubble)] overflow-hidden border border-[var(--ui-border-subtle)]">
        <div className="px-3 py-2 flex items-center gap-2">
          <span className={`text-[0.8125rem] truncate flex-1 min-w-0 ${isError ? "text-[color:var(--ui-danger)]" : "text-[color:var(--ui-accent)]"}`}>
            {filePath}
          </span>
          <span className="shrink-0 text-xs flex items-center gap-2">
            <span className="text-[color:var(--ui-success)]">+{additions}</span>
            <span className="text-[color:var(--ui-danger)]">-{deletions}</span>
          </span>
        </div>

        <div
          ref={scrollRef}
          onScroll={updateScrollIndicators}
          className="border-t border-[var(--ui-border)] max-h-[250px] overflow-auto"
          style={{ boxShadow: edgeShadows || "none" }}
        >
          <MultiFileDiff
            oldFile={oldFile}
            newFile={newFile}
            options={inlineDiffOptions}
          />
        </div>
      </div>
    </div>
  );
});

export const ToolExecution = memo(function ToolExecution({
  chunk,
  projectPath,
  resolvedDiffData,
}: ToolExecutionProps) {
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const { title, toolKind, input, status, output } = chunk;
  const diffs = chunk.diffs?.length ? chunk.diffs : (chunk.diffData ? [chunk.diffData] : []);
  const diffData = diffs[diffs.length - 1];

  const isEditOp = toolKind === "edit" || toolKind === "delete" || toolKind === "move" || diffData != null;
  const isCompletedEditOp = isEditOp && diffData && (status === "completed" || status === "error");
  const editedFilePath = diffData ? stripProjectPath(diffData.filePath, projectPath) : "";
  const editedFileName = editedFilePath ? getFileName(editedFilePath) : "";
  const diffStats = diffData ? countLineChanges(diffData.originalContent, diffData.newContent) : null;

  if (isCompletedEditOp && diffStats && diffData) {
    return (
      <InlineDiffCollapsible
        filePath={editedFilePath || diffData.filePath}
        fileName={editedFileName || editedFilePath || diffData.filePath}
        originalContent={resolvedDiffData?.originalContent ?? diffData.originalContent ?? ""}
        newContent={resolvedDiffData?.modifiedContent ?? diffData.newContent}
        additions={diffStats.additions}
        deletions={diffStats.deletions}
        isError={status === "error"}
      />
    );
  }

  if (isEditOp && status === "pending" && diffData) {
    return (
      <div className="my-1 text-[0.75rem] leading-5">
        <DiffView diffData={diffData} />
        <span className="text-[color:var(--ui-text-muted)]">等待批准...</span>
      </div>
    );
  }

  if (isEditOp && status === "in_progress") {
    const path = stripProjectPath(
      diffData?.filePath ||
      (input?.filePath as string) ||
      (input?.path as string) || "file",
      projectPath,
    );
    return (
      <div className="my-0.5 text-[0.75rem] leading-5">
        <span className="text-[color:var(--ui-text-muted)]">正在编辑 {getFileName(path)}...</span>
      </div>
    );
  }

  const displayName = formatToolDisplay(title, toolKind, input, projectPath);
  const statusTextClass =
    status === "error" || status === "rejected"
      ? "text-[color:var(--ui-danger)]"
      : "text-[color:var(--ui-text-muted)]";

  return (
    <div className="my-0.5 text-[0.75rem] leading-5">
      <div className="flex items-center gap-2 min-w-0">
        <span className={`${statusTextClass} truncate`}>{displayName}</span>
        {status === "rejected" && (
          <span className="shrink-0 px-1.5 py-0.5 text-[0.625rem] leading-3 rounded bg-[color:var(--ui-danger)]/10 text-[color:var(--ui-danger)]">
            已被用户拒绝执行
          </span>
        )}
        {status === "error" && toolKind === "execute" && (
          <span className="shrink-0 px-1.5 py-0.5 text-[0.625rem] leading-3 rounded bg-[color:var(--ui-danger)]/10 text-[color:var(--ui-danger)]">
            命令执行失败
          </span>
        )}
        {status === "error" && toolKind !== "execute" && output && (
          <span className="truncate text-[color:var(--ui-danger)] opacity-80">{output.slice(0, 80)}</span>
        )}
      </div>
      {(output || input) && (
        <div className="mt-1">
          <button
            type="button"
            onClick={() => setDetailsExpanded((prev) => !prev)}
            className="inline-flex items-center gap-1 text-[0.6875rem] text-[color:var(--ui-text-dim)] transition-[color,transform] duration-150 hover:text-[color:var(--ui-text-muted)] active:scale-[0.98]"
          >
            {detailsExpanded ? "隐藏细节" : "显示细节"}
            <ChevronDown className={`size-3 transition-transform duration-200 ease-out ${detailsExpanded ? "rotate-180" : ""}`} />
          </button>
          {detailsExpanded && (
            <div className="mt-1 animate-in fade-in-0 slide-in-from-top-1 duration-150 ease-out space-y-1">
              {input && Object.keys(input).length > 0 && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--ui-accent-bubble)] px-2 py-1 text-[0.6875rem] leading-5 text-[color:var(--ui-text-muted)]">
                  {JSON.stringify(input, null, 2)}
                </pre>
              )}
              {output && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-[var(--ui-accent-bubble)] px-2 py-1 text-[0.6875rem] leading-5 text-[color:var(--ui-text-muted)]">
                  {output}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
});
