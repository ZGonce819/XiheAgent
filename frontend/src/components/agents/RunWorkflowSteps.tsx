import { useState, useEffect } from "react";
import { ChevronDown, ChevronUp, Brain, Wrench, Search, Cog, CheckCircle2, Circle, AlertCircle, Loader2 } from "lucide-react";

import type { RunEvent, RunEventKind, RunEventStatus } from "@/lib/agents/types";

const KIND_CONFIG: Record<RunEventKind, { icon: typeof Brain; label: string; color: string }> = {
  think: { icon: Brain, label: "思考", color: "text-purple-400" },
  execute: { icon: Wrench, label: "执行", color: "text-blue-400" },
  search: { icon: Search, label: "搜索", color: "text-cyan-400" },
  other: { icon: Cog, label: "处理", color: "text-gray-400" },
  todo: { icon: CheckCircle2, label: "任务", color: "text-green-400" },
};

const STATUS_CONFIG: Record<RunEventStatus, { icon: typeof Circle; label: string }> = {
  pending: { icon: Circle, label: "等待中" },
  in_progress: { icon: Loader2, label: "进行中" },
  completed: { icon: CheckCircle2, label: "已完成" },
  error: { icon: AlertCircle, label: "失败" },
};

function getStepStatusColor(status: RunEventStatus): string {
  switch (status) {
    case "completed":
      return "text-green-400";
    case "in_progress":
      return "text-blue-400";
    case "error":
      return "text-red-400";
    default:
      return "text-[color:var(--ui-text-dim)]";
  }
}

function StepIcon({ status, kind }: { status: RunEventStatus; kind: RunEventKind }) {
  if (status === "in_progress") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />;
  }

  const kindConfig_ = KIND_CONFIG[kind];
  const KindIcon = kindConfig_.icon;

  return (
    <KindIcon className={`h-3.5 w-3.5 ${status === "completed" ? "text-green-400" : status === "error" ? "text-red-400" : kindConfig_.color}`} />
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
    <div className={`rounded-xl border border-[var(--ui-border)] bg-[var(--ui-panel)] overflow-hidden ${className}`}>
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 flex items-center justify-between gap-2 hover:bg-[var(--ui-panel-2)] transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          {isRunning && !hasError && (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400 shrink-0" />
          )}
          {hasError && (
            <AlertCircle className="h-3.5 w-3.5 text-red-400 shrink-0" />
          )}
          {!isRunning && !hasError && (
            <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />
          )}
          <span className="text-xs text-[color:var(--ui-text-muted)] truncate">
            {headerText}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-[color:var(--ui-text-dim)]">
            {completedCount}/{runEvents.length}
          </span>
          {isExpanded ? (
            <ChevronUp className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)]" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)]" />
          )}
        </div>
      </button>

      {isExpanded && (
        <div className="border-t border-[var(--ui-border)] px-3 py-2">
          <div className="space-y-1">
            {runEvents.map((event, index) => {
              const kindCfg = KIND_CONFIG[event.kind] || KIND_CONFIG.other;
              const statusColor = getStepStatusColor(event.status);

              return (
                <div
                  key={event.id || index}
                  className="flex items-center gap-2 py-1 text-xs"
                >
                  <StepIcon status={event.status} kind={event.kind} />
                  <span className={`truncate ${statusColor}`}>
                    {event.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-[color:var(--ui-text-dim)] ml-auto">
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
