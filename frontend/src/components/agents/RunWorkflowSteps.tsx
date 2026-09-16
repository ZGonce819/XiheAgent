import { useState, useEffect } from "react";
import { ChevronDown, ChevronUp, Brain, Wrench, Search, Cog, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

import type { RunEvent, RunEventKind, RunEventStatus } from "@/lib/agents/types";

const KIND_CONFIG: Record<RunEventKind, { icon: typeof Brain; label: string; color: string }> = {
  think: { icon: Brain, label: "思考", color: "text-[color:var(--ui-text-muted)]" },
  execute: { icon: Wrench, label: "执行", color: "text-[color:var(--ui-accent)]" },
  search: { icon: Search, label: "搜索", color: "text-[color:var(--ui-text-muted)]" },
  other: { icon: Cog, label: "处理", color: "text-[color:var(--ui-text-muted)]" },
  todo: { icon: CheckCircle2, label: "任务", color: "text-[color:var(--ui-success)]" },
};

function getStepStatusColor(status: RunEventStatus): string {
  switch (status) {
    case "completed":
      return "text-[color:var(--ui-text)]";
    case "in_progress":
      return "text-[color:var(--ui-accent)]";
    case "error":
      return "text-[color:var(--ui-danger)]";
    default:
      return "text-[color:var(--ui-text-dim)]";
  }
}

function StepIcon({ status, kind }: { status: RunEventStatus; kind: RunEventKind }) {
  if (status === "in_progress") {
    return <Loader2 className="agents-spinner h-3.5 w-3.5 text-[color:var(--ui-accent)]" />;
  }

  const kindConfig_ = KIND_CONFIG[kind];
  const KindIcon = kindConfig_.icon;

  return (
    <KindIcon className={`h-3.5 w-3.5 ${status === "completed" ? "text-[color:var(--ui-success)]" : status === "error" ? "text-[color:var(--ui-danger)]" : kindConfig_.color}`} />
  );
}

interface RunWorkflowStepsProps {
  runEvents: RunEvent[];
  isRunning: boolean;
  className?: string;
}

export function RunWorkflowSteps({ runEvents, isRunning, className = "" }: RunWorkflowStepsProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    if (isRunning) {
      setIsExpanded(true);
    }
  }, [isRunning]);

  if (!runEvents || runEvents.length === 0) return null;

  const completedCount = runEvents.filter((e) => e.status === "completed").length;
  const hasError = runEvents.some((e) => e.status === "error");
  const inProgressEvent = runEvents.find((e) => e.status === "in_progress");

  const headerText = hasError
    ? "运行出错"
    : isRunning
      ? inProgressEvent
        ? inProgressEvent.title
        : "运行中..."
      : `已完成 ${completedCount}/${runEvents.length} 步`;

  return (
    <div className={`agents-task-panel my-1 overflow-hidden rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] ${className}`}>
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2.5 flex items-center justify-between gap-3 transition-colors hover:bg-[var(--ui-panel-2)]"
      >
        <div className="flex items-center gap-2 min-w-0">
          {isRunning && !hasError && (
            <Loader2 className="agents-spinner size-4 shrink-0 text-[color:var(--ui-accent)]" />
          )}
          {hasError && (
            <AlertCircle className="size-4 shrink-0 text-[color:var(--ui-danger)]" />
          )}
          {!isRunning && !hasError && (
            <CheckCircle2 className="size-4 shrink-0 text-[color:var(--ui-success)]" />
          )}
          <span className="text-[0.8125rem] text-[color:var(--ui-text-muted)] truncate">
            {headerText}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[0.625rem] text-[color:var(--ui-text-dim)]">
            {completedCount}/{runEvents.length}
          </span>
          {isExpanded ? (
            <ChevronUp className="size-4 text-[color:var(--ui-text-dim)]" />
          ) : (
            <ChevronDown className="size-4 text-[color:var(--ui-text-dim)]" />
          )}
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-[var(--ui-border)] px-4 py-2">
          <div className="space-y-1">
            {runEvents.map((event, index) => {
              const kindCfg = KIND_CONFIG[event.kind] || KIND_CONFIG.other;
              const statusColor = getStepStatusColor(event.status);

              return (
                <div
                  key={event.id || index}
                  className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-x-2 border-b border-[var(--ui-border-subtle)] py-2.5 text-[0.8125rem] last:border-0"
                >
                  <StepIcon status={event.status} kind={event.kind} />
                  <div className="min-w-0">
                    <p className={statusColor}>{event.title}</p>
                    {event.detail && (
                      <p className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[color:var(--ui-text-dim)]">
                        {event.detail}
                      </p>
                    )}
                  </div>
                  <span className="shrink-0 text-[0.625rem] text-[color:var(--ui-text-dim)] ml-auto">
                    {kindCfg.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
