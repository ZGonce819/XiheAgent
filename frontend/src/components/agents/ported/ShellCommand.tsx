import { memo, useState, useRef, useCallback, useLayoutEffect } from "react";
import type { ToolExecutionChunk } from "@/lib/agents/types";

interface ShellCommandProps {
  chunk: ToolExecutionChunk;
  projectPath?: string;
}

function getHeaderText(chunk: ToolExecutionChunk): string {
  const cmd = (chunk.input?.command as string) || chunk.title || "命令";
  const truncated = cmd.length > 80 ? cmd.slice(0, 80) + "..." : cmd;
  if (chunk.status === "in_progress") return `正在运行 ${truncated}`;
  if (chunk.status === "pending") return `准备运行 ${truncated}`;
  if (chunk.status === "rejected") return `命令已拒绝：${truncated}`;
  if (chunk.status === "error") return truncated;
  return `命令完成：${truncated}`;
}

export const ShellCommand = memo(function ShellCommand({
  chunk,
}: ShellCommandProps) {
  const [expanded, setExpanded] = useState(false);
  const [scrolledFromTop, setScrolledFromTop] = useState(false);
  const [scrolledFromBottom, setScrolledFromBottom] = useState(true);
  const outputRef = useRef<HTMLDivElement>(null);

  const handleOutputScroll = useCallback(() => {
    const el = outputRef.current;
    if (!el) return;
    setScrolledFromTop(el.scrollTop > 0);
    setScrolledFromBottom(el.scrollTop < el.scrollHeight - el.clientHeight - 1);
  }, []);

  const command = (chunk.input?.command as string) || chunk.title || "";
  const cwd = (chunk.input?.cwd as string) || "";
  const output = chunk.output || "";
  const headerText = getHeaderText(chunk);

  useLayoutEffect(() => {
    handleOutputScroll();
  }, [handleOutputScroll, output, expanded]);

  const outputEdgeShadows = [
    scrolledFromTop ? "inset 0 12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
    scrolledFromBottom ? "inset 0 -12px 10px -10px rgba(42, 63, 95, 0.95)" : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center gap-2 py-1 text-left hover:opacity-90 transition-opacity"
      >
        <span className={`text-[0.75rem] truncate flex-1 min-w-0 ${chunk.status === "rejected" ? "text-[color:var(--ui-danger)]" : "text-[color:var(--ui-text-muted)]"}`}>
          {headerText}
        </span>
        {(chunk.status === "rejected" || chunk.status === "error") && (
          <span className="shrink-0 rounded bg-[color:var(--ui-danger)]/10 px-1.5 py-0.5 text-[0.625rem] leading-3 text-[color:var(--ui-danger)]">
            {chunk.status === "error" ? "命令执行失败" : "已被用户拒绝执行"}
          </span>
        )}
        <span
          className="text-[color:var(--ui-text-dim)] text-xs shrink-0 transition-transform"
          style={{ transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }}
        >
          ▾
        </span>
      </button>

      {expanded && (
        <div className="mt-1 flex max-h-[250px] flex-col overflow-hidden rounded-lg bg-[var(--ui-accent-bubble)]">
          <div className="px-3 pt-2 pb-1 font-mono text-xs shrink-0">
            <div className="text-[color:var(--ui-text-dim)] mb-2">bash</div>
            {cwd && <div className="text-[color:var(--ui-text-dim)] mb-2">cwd: {cwd}</div>}
            <div className="text-[color:var(--ui-text)] font-semibold whitespace-pre overflow-x-auto">
              <span className="text-[color:var(--ui-text-dim)]">$ </span>
              {command}
            </div>
          </div>
          {output && (
            <div
              ref={outputRef}
              onScroll={handleOutputScroll}
              className="min-h-0 flex-1 overflow-auto px-3 pb-1"
              style={{ boxShadow: outputEdgeShadows || "none" }}
            >
              <pre className="mt-1 text-[color:var(--ui-text-muted)] whitespace-pre font-mono text-xs w-max min-w-full">
                {output}
              </pre>
            </div>
          )}
          <div className="px-3 py-1.5 flex justify-end shrink-0">
            {chunk.status === "in_progress" && (
              <span className="text-xs text-[color:var(--ui-warning)]">运行中...</span>
            )}
            {chunk.status === "completed" && (
              <span className="text-[color:var(--ui-text-muted)] text-xs">✓ 成功</span>
            )}
            {chunk.status === "error" && (
              <span className="text-xs text-[color:var(--ui-danger)]">失败</span>
            )}
            {chunk.status === "pending" && (
              <span className="text-xs text-[color:var(--ui-warning)]">等待批准...</span>
            )}
            {chunk.status === "rejected" && (
              <span className="text-xs text-[color:var(--ui-danger)]">未执行 · 已被用户拒绝执行</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
});
