import { useState } from "react"
import {
  AlertTriangle,
  ChevronDown,
  LoaderCircle,
  Sparkle,
  Terminal,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface CommandApprovalDialogProps {
  open: boolean
  command: string
  reason: string
  error?: string | null
  explanation?: string | null
  explanationError?: string | null
  explaining?: boolean
  onOpenChange?: (open: boolean) => void
  onExplain: () => void
  onApprove: () => void
  onReject: () => void
}

export function CommandApprovalDialog({
  open,
  command,
  reason,
  error,
  explanation,
  explanationError,
  explaining = false,
  onExplain,
  onApprove,
  onReject,
}: CommandApprovalDialogProps) {
  const [showExplanation, setShowExplanation] = useState(false)
  const [expandedCommand, setExpandedCommand] = useState(false)

  if (!open) return null

  return (
    <section
      aria-labelledby="command-approval-title"
      className="command-approval-panel mb-3 overflow-hidden rounded-lg border border-[var(--ui-border)] bg-[var(--ui-panel)] shadow-[0_8px_24px_rgba(0,0,0,0.08)]"
    >
      <div className="flex items-start gap-3 px-4 pt-4 pb-3 sm:px-5 sm:pt-5">
        <AlertTriangle
          className="command-approval-alert mt-0.5 size-4 shrink-0 text-[color:var(--ui-warning,#a16207)]"
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <h2
            id="command-approval-title"
            className="text-[0.875rem] font-semibold text-[var(--ui-text)]"
          >
            XiheAgent 想要执行一个操作
          </h2>
          <p className="mt-1 text-[0.8125rem] leading-5 text-[color:var(--ui-text-muted)]">
            请确认命令内容后再决定是否执行
          </p>
        </div>
      </div>

      <div className="command-approval-command mx-4 mb-4 overflow-hidden rounded-md bg-[var(--ui-panel-2)] sm:mx-5">
        <div
          className={cn(
            "relative overflow-hidden px-4 py-3",
            !expandedCommand && "max-h-24"
          )}
        >
          <Terminal
            className="mr-2 inline size-4 text-[color:var(--ui-text-dim)]"
            aria-hidden
          />
          <code className="font-mono text-[0.8125rem] leading-5 break-all whitespace-pre-wrap text-[color:var(--ui-text-muted)]">
            {command}
          </code>
          {!expandedCommand && command.length > 180 ? (
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-[var(--ui-panel-2)] to-transparent" />
          ) : null}
        </div>
        {command.length > 180 ? (
          <button
            type="button"
            className="flex w-full items-center gap-1 border-t border-[var(--ui-border)] px-4 py-2 text-left text-xs text-[color:var(--ui-text-muted)] hover:text-[var(--ui-text)] focus-visible:ring-2 focus-visible:ring-[var(--ui-accent)] focus-visible:outline-none focus-visible:ring-inset"
            onClick={() => setExpandedCommand((value) => !value)}
            aria-expanded={expandedCommand}
          >
            {expandedCommand ? "收起完整内容" : "展开完整内容"}
            <ChevronDown
              className={cn(
                "size-3.5 transition-transform duration-200",
                expandedCommand && "rotate-180"
              )}
            />
          </button>
        ) : null}
      </div>

      {showExplanation ? (
        <div
          aria-live="polite"
          className="command-approval-explanation mx-4 mb-4 rounded-md border border-[var(--ui-border)] bg-[var(--ui-bg)] px-3 py-2.5 text-[0.8125rem] leading-5 text-[color:var(--ui-text-muted)] sm:mx-5"
        >
          <p className="font-medium text-[var(--ui-text)]">为什么需要审批？</p>
          {explaining ? (
            <p className="mt-1 flex items-center gap-1.5">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
              正在生成解释...
            </p>
          ) : explanationError ? (
            <p className="mt-1 text-[color:var(--ui-danger)]" role="alert">
              {explanationError}
            </p>
          ) : (
            <p className="mt-1">
              {explanation || reason || "该命令可能改变工作区、访问网络或影响正在运行的进程。"}
            </p>
          )}
          <p className="mt-1">
            “允许一次”仅授权当前展示的命令；后续同类操作仍按设置中的审批规则处理。
          </p>
        </div>
      ) : null}

      {error ? (
        <p
          role="alert"
          className="mx-4 mb-3 text-xs text-[color:var(--ui-danger)] sm:mx-5"
        >
          {error}
        </p>
      ) : null}

      <div className="command-approval-actions flex flex-col gap-2 border-t border-[var(--ui-border)] px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-start text-[0.8125rem] text-[color:var(--ui-text-muted)]"
          disabled={explaining}
          onClick={() => {
            if (showExplanation && !explanationError) {
              setShowExplanation(false)
              return
            }
            setShowExplanation(true)
            onExplain()
          }}
        >
          <Sparkle className="size-3.5" aria-hidden />
          {explaining ? "解释中" : showExplanation && !explanationError ? "收起解释" : explanationError ? "重试解释" : "解释"}
        </Button>
        <div className="grid grid-cols-2 gap-2 sm:flex">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-[0.8125rem]"
            onClick={onReject}
          >
            拒绝
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onApprove}
            className="bg-[var(--ui-accent)] text-[0.8125rem] text-white hover:opacity-90"
          >
            允许一次
          </Button>
        </div>
      </div>
    </section>
  )
}
