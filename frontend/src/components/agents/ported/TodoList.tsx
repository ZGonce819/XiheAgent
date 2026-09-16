import { useRef, useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  Circle,
  ListChecks,
  Loader2,
} from "lucide-react";
import type { TodoItem } from "@/lib/agents/types";

interface TodoListProps {
  todos: TodoItem[];
  className?: string;
  runActive?: boolean;
}

function StatusIcon({ status }: { status: TodoItem["status"] }) {
  switch (status) {
    case "pending":
      return <Circle className="size-4 text-[color:var(--ui-text-dim)] shrink-0" />;
    case "in_progress":
      return <Loader2 className="agents-spinner size-4 text-[color:var(--ui-accent)] shrink-0" />;
    case "completed":
      return <CheckCircle2 className="size-4 shrink-0 text-[color:var(--ui-success)]" />;
  }
}

const MAX_HEIGHT = 220;

export function TodoList({ todos, className = "", runActive = false }: TodoListProps) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevRunActiveRef = useRef(runActive);

  useEffect(() => {
    if (prevRunActiveRef.current && !runActive) {
      setCollapsed(true);
    }
    prevRunActiveRef.current = runActive;
  }, [runActive]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const firstActive = todos.findIndex((t) => t.status === "in_progress");
    const targetIndex = firstActive !== -1 ? firstActive : todos.length - 1;
    const targetEl = el.children[targetIndex] as HTMLElement | undefined;
    targetEl?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [todos]);

  if (todos.length === 0) return null;

  const completed = todos.filter((t) => t.status === "completed").length;

  return (
    <div className={`font-sans text-[0.8125rem] rounded-lg border border-[var(--ui-border)] bg-[var(--ui-accent-bubble)] overflow-hidden ${className}`}>
      <button
        type="button"
        className="group w-full px-3 py-2 flex items-center gap-2 text-left transition-colors hover:bg-white/5"
        onClick={() => setCollapsed((c) => !c)}
      >
        <ListChecks className="size-4 text-[color:var(--ui-text-muted)] shrink-0" />
        <span className="text-[color:var(--ui-text-muted)] truncate text-[0.8125rem]">
          {completed} / {todos.length} 项任务已完成
        </span>
        <ChevronDown className={`ml-auto size-4 shrink-0 text-[color:var(--ui-text-dim)] transition-transform duration-200 ease-out ${collapsed ? "-rotate-90" : ""}`} />
      </button>

      {!collapsed && (
        <div
          ref={scrollRef}
          className="animate-in fade-in-0 slide-in-from-top-1 duration-150 ease-out border-t border-[var(--ui-border)] px-3 py-2 space-y-1 overflow-y-auto"
          style={{ maxHeight: MAX_HEIGHT }}
        >
          {todos.map((todo, index) => (
            <div
              key={index}
              className={`flex items-start gap-2 py-0.5 animate-in fade-in-0 slide-in-from-left-1 duration-200 ${todo.status === "completed" ? "opacity-75" : ""}`}
            >
              <StatusIcon status={todo.status} />
              <span
                className={`text-[0.8125rem] leading-5 ${
                  todo.status === "completed"
                    ? "text-[color:var(--ui-text-dim)] line-through"
                    : todo.status === "in_progress"
                      ? "text-[color:var(--ui-text)]"
                      : "text-[color:var(--ui-text-muted)]"
                }`}
              >
                <span className="mr-1 text-[color:var(--ui-text-dim)]">{index + 1}.</span>
                {todo.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
