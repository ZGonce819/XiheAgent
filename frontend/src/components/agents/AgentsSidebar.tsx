import { Link } from "@tanstack/react-router"
import {
  CalendarBlankIcon,
  ChatCircleIcon,
  GitBranchIcon,
  PlusIcon,
  WrenchIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { useState, useMemo, type ComponentType, type SVGProps } from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentSource, AgentThread } from "@/lib/agents/types"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { groupThreads } from "@/lib/agents/api"
import { useAgentThreads, useDeleteAgentThread } from "@/lib/agents/queries"
import { cn } from "@/lib/utils"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"

type SourceIcon = ComponentType<SVGProps<SVGSVGElement>>

const SOURCE_META: Record<AgentSource, { icon: SourceIcon; label: string }> = {
  dashboard: { icon: ChatCircleIcon, label: "从对话启动" },
  gitee: { icon: GitBranchIcon, label: "从 Gitee 触发" },
  github: { icon: IoLogoGithub, label: "从 GitHub 触发" },
  slack: { icon: IoLogoSlack, label: "从 Slack 触发" },
  linear: { icon: SiLinear, label: "从 Linear 触发" },
  schedule: { icon: CalendarBlankIcon, label: "由定时任务触发" },
}

interface AgentsSidebarProps {
  user: SessionUser
  activeThreadId?: string
}

const NAV = [
  { to: "/automations", label: "自动化任务", icon: CalendarBlankIcon },
  { to: "/skills", label: "技能接口", icon: WrenchIcon },
] as const

export function AgentsSidebar({ user, activeThreadId }: AgentsSidebarProps) {
  const threadsQuery = useAgentThreads()
  const threads = threadsQuery.data ?? []
  const groups = groupThreads(threads)
  const layout = useSidebarLayout()

  return (
    <SidebarFrame
      {...layout}
      className="border-r border-[var(--ui-border)] bg-[var(--ui-sidebar)]"
    >
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="font-heading text-sm font-medium tracking-tight text-[var(--ui-text)]"
        >
          XiheAgent
        </Link>
        <SidebarCollapseButton onToggle={layout.toggle} />
      </div>

      <div className="px-2 pb-1">
        <Link
          to="/agents"
          onClick={layout.closeOnMobile}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-[var(--ui-text)] transition-colors hover:bg-[var(--ui-sidebar-hover)]"
        >
          <PlusIcon className="size-4" />
          新任务
        </Link>
      </div>

      <nav className="flex flex-col gap-0.5 px-2 pb-4">
        {NAV.map((item) => {
          const Icon = item.icon
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={layout.closeOnMobile}
              className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-[var(--ui-text-muted)] transition-colors hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)]"
              activeProps={{
                className:
                  "bg-[var(--ui-sidebar-hover)] !text-[var(--ui-text)] font-medium",
              }}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          )
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <ThreadGroup
          label="今天"
          threads={groups.today}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
        <ThreadGroup
          label="最近 30 天"
          threads={groups.last30}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
        <ThreadGroup
          label="更早"
          threads={groups.older}
          activeThreadId={activeThreadId}
          onNavigate={layout.closeOnMobile}
        />
      </div>

      <div className="p-2">
        <SidebarUserMenu user={user} showSettingsLink />
      </div>
    </SidebarFrame>
  )
}

function ThreadGroup({
  label,
  threads,
  activeThreadId,
  onNavigate,
}: {
  label: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
}) {
  if (threads.length === 0) return null

  return (
    <div className="mb-3">
      <div className="px-2 py-1 text-[0.625rem] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
        {label}
      </div>
      {threads.map((thread) => (
        <ThreadRow
          key={thread.id}
          thread={thread}
          isActive={thread.id === activeThreadId}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  )
}

function formatLastMessageTime(messages: Array<{ timestamp: string }>): string {
  if (messages.length === 0) return ""
  const last = messages[messages.length - 1]
  if (!last) return ""
  const date = new Date(last.timestamp)
  if (Number.isNaN(date.getTime())) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  if (isToday) return `${pad(date.getHours())}:${pad(date.getMinutes())}`
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function getDisplayRepo(repo: string): string {
  if (!/^https?:\/\//i.test(repo)) return repo
  try {
    const parts = new URL(repo).pathname.split("/").filter(Boolean)
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : repo
  } catch {
    return repo
  }
}

function ThreadRow({
  thread,
  isActive,
  onNavigate,
}: {
  thread: AgentThread
  isActive: boolean
  onNavigate?: () => void
}) {
  const deleteThread = useDeleteAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null
  const isDeleting =
    deleteThread.isPending && deleteThread.variables === thread.id

  const onDelete = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isDeleting) return
    setDeleteOpen(true)
  }

  const source =
    thread.source && thread.source !== "dashboard"
      ? SOURCE_META[thread.source]
      : null
  const SourceIcon = source?.icon

  const lastMessageTime = useMemo(
    () => formatLastMessageTime(thread.messages),
    [thread.messages]
  )
  const displayRepo = useMemo(
    () => getDisplayRepo(thread.repoFullName || thread.repo || ""),
    [thread.repoFullName, thread.repo]
  )
  const envLabel = thread.backendType === "sandbox" ? "沙箱" : "本地"

  return (
    <>
      <Link
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        onClick={onNavigate}
        className={cn(
          "group mb-0.5 flex flex-col gap-0.5 rounded-lg px-2.5 py-1.5 transition-colors",
          isActive
            ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
            : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
          isDeleting && "opacity-50"
        )}
      >
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "-mt-0.5 size-2 shrink-0 self-center rounded-full",
              thread.status === "running"
                ? "animate-pulse bg-[var(--ui-accent)]"
                : thread.status === "finished"
                  ? "bg-[var(--ui-accent)]"
                  : "bg-[var(--ui-border)]"
            )}
          />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              {source && SourceIcon && (
                <SourceIcon
                  className="size-3.5 shrink-0 text-[var(--ui-text-dim)]"
                  aria-label={source.label}
                >
                  <title>{source.label}</title>
                </SourceIcon>
              )}
              <span className="min-w-0 flex-1 truncate text-xs">
                {thread.title}
              </span>
              {badge && (
                <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 py-0.5 text-[0.625rem] text-[var(--ui-text-dim)] group-hover:hidden">
                  {badge}
                </span>
              )}
              <button
                type="button"
                aria-label="删除会话"
                onClick={onDelete}
                disabled={isDeleting}
                className="hidden size-4 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] group-hover:flex hover:bg-[var(--ui-panel-2)] hover:text-[var(--ui-text)]"
              >
                <XIcon className="size-3" weight="bold" />
              </button>
            </div>
            {(lastMessageTime || displayRepo) && (
              <div className="flex items-center gap-1 text-[0.5625rem] text-[var(--ui-text-dim)]">
                {lastMessageTime && <span>{lastMessageTime}</span>}
                {displayRepo && (
                  <>
                    {lastMessageTime && <span className="opacity-50">·</span>}
                    <span className="truncate">{displayRepo}</span>
                  </>
                )}
                {lastMessageTime && <span className="opacity-50">·</span>}
                <span>{envLabel}</span>
              </div>
            )}
          </div>
        </div>
      </Link>
      <ConfirmDialog
        open={deleteOpen}
        title="删除会话"
        description={`将永久删除“${thread.title}”及其历史记录，此操作无法撤销。`}
        confirmText="删除"
        destructive
        busy={isDeleting}
        onOpenChange={setDeleteOpen}
        onConfirm={() =>
          deleteThread.mutate(thread.id, {
            onSuccess: () => setDeleteOpen(false),
          })
        }
      />
    </>
  )
}

export function AgentsShell({
  user,
  activeThreadId,
  children,
}: {
  user: SessionUser
  activeThreadId?: string
  children: React.ReactNode
}) {
  return (
    <div className="agents-ui flex h-svh overflow-hidden bg-[var(--ui-bg)]">
      <AgentsSidebar user={user} activeThreadId={activeThreadId} />
      <div className="flex min-w-0 flex-1">{children}</div>
    </div>
  )
}
