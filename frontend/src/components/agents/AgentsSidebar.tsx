import { Link, useNavigate } from "@tanstack/react-router"
import { FolderOpen, Pencil } from "lucide-react"
import {
  CalendarBlankIcon,
  ChatCircleIcon,
  CaretDownIcon,
  GitBranchIcon,
  PlusIcon,
  WrenchIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub, IoLogoSlack } from "react-icons/io5"
import { SiLinear } from "react-icons/si"
import { Dialog } from "@base-ui/react/dialog"
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useMemo,
  type ComponentType,
  type SVGProps,
} from "react"

import type { SessionUser } from "@/lib/api"
import type { AgentSource, AgentThread } from "@/lib/agents/types"
import { SidebarUserMenu } from "@/components/SidebarUserMenu"
import {
  SidebarCollapseButton,
  SidebarFrame,
  useSidebarLayout,
} from "@/components/sidebar-layout"
import { useAgentThreads, useDeleteAgentThread, useRenameProject } from "@/lib/agents/queries"
import {
  claimAnonymousThreadReveal,
  consumeThreadReveal,
  hasAnonymousThreadReveal,
  subscribeThreadReveal,
} from "@/lib/agents/threadReveal"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
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

/**
 * 取走「刚发送消息」的对话 id，用于给侧边栏对话导航项播放出现动画。
 * 信号在用户按下发送时就会到达，因此动画立即触发，不等后端响应。
 *
 * 订阅只用于触发一次重渲染，真正的消费放在 effect 里：
 * 每次信号到达（signal 变化）或列表变化时尝试消费一次，
 * 只有真正消费到信号才会更新需要播放动画的 id。
 */
function useSentThreadReveal(
  threads: Array<AgentThread>,
  activeThreadId?: string
): string | null {
  const [signal, setSignal] = useState(0)
  const [revealed, setRevealed] = useState<{ id: string; seq: number } | null>(null)

  useEffect(
    () =>
      subscribeThreadReveal(() => {
        setSignal((tick) => tick + 1)
      }),
    []
  )

  useEffect(() => {
    // 1) 直接命中：发送时已经知道 threadId（在已有对话里继续发消息）
    if (activeThreadId) {
      const seq = consumeThreadReveal(activeThreadId)
      if (seq !== null) {
        setRevealed({ id: activeThreadId, seq })
        return
      }
    }
    // 2) 匿名信号兜底：新任务页首次发送时还没有 threadId，
    //    正常路径已由 queries.ts 在拿到 threadId 时认领；这里作为兜底，
    //    等侧边栏列表出现新对话后交给最新的一条。
    if (hasAnonymousThreadReveal() && threads.length > 0) {
      const newest = [...threads].sort((a, b) => b.updatedAt - a.updatedAt)[0]
      if (newest && claimAnonymousThreadReveal(newest.id)) {
        setRevealed({ id: newest.id, seq: 1 })
      }
    }
  }, [signal, threads, activeThreadId])

  // 用 `${id}\0${seq}` 让同一条对话的多次发送也能各自重播动画
  return revealed ? `${revealed.id}\u0000${revealed.seq}` : null
}

export function AgentsSidebar({ user, activeThreadId }: AgentsSidebarProps) {
  const threadsQuery = useAgentThreads()
  const threads = threadsQuery.data ?? []
  const layout = useSidebarLayout()
  // 最近一次发送消息后需要播放出现动画的对话（发送瞬间标记，无需等后端）
  const revealKey = useSentThreadReveal(threads, activeThreadId)

  // 按项目名分组，无项目的对话直接显示在顶层
  const { projectGroups, ungroupedThreads } = useMemo(() => {
    const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt)
    const projectMap = new Map<string, Array<AgentThread>>()
    const ungrouped: Array<AgentThread> = []

    for (const thread of sorted) {
      const projectName = thread.projectName?.trim()
      if (projectName) {
        if (!projectMap.has(projectName)) {
          projectMap.set(projectName, [])
        }
        projectMap.get(projectName)!.push(thread)
      } else {
        ungrouped.push(thread)
      }
    }

    // 按最近活跃时间排序项目合集（每个合集内第一条对话的 updatedAt 最新）
    const sortedProjects = Array.from(projectMap.entries()).sort((a, b) => {
      const aLatest = a[1][0]?.updatedAt ?? 0
      const bLatest = b[1][0]?.updatedAt ?? 0
      return bLatest - aLatest
    })

    return {
      projectGroups: sortedProjects,
      ungroupedThreads: ungrouped,
    }
  }, [threads])

  const hasAnyContent = projectGroups.length > 0 || ungroupedThreads.length > 0

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
        {/* 项目合集 */}
        {projectGroups.length > 0 && (
          <div className="px-2 py-1 text-[0.625rem] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
            项目
          </div>
        )}
        {projectGroups.map(([projectName, projectThreads]) => (
          <ProjectGroup
            key={projectName}
            projectName={projectName}
            threads={projectThreads}
            activeThreadId={activeThreadId}
            onNavigate={layout.closeOnMobile}
            revealKey={revealKey}
          />
        ))}

        {/* 最近对话 */}
        {ungroupedThreads.length > 0 && (
          <div className="px-2 py-1 text-[0.625rem] font-semibold tracking-wide text-[var(--ui-text-dim)] uppercase">
            最近
          </div>
        )}
        {ungroupedThreads.map((thread) => (
          <ThreadRow
            key={thread.id}
            thread={thread}
            isActive={thread.id === activeThreadId}
            onNavigate={layout.closeOnMobile}
            revealKey={revealKey}
          />
        ))}

        {/* 空状态 */}
        {!hasAnyContent && (
          <div className="mb-3">
            <Link
              to="/agents"
              onClick={layout.closeOnMobile}
              className="mx-2 flex items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-[var(--ui-border)] px-3 py-3 text-xs text-[var(--ui-text-dim)] transition-colors hover:border-[var(--ui-accent)] hover:text-[var(--ui-accent)]"
            >
              <PlusIcon className="size-3.5" />
              新任务
            </Link>
          </div>
        )}
      </div>

      <div className="p-2">
        <SidebarUserMenu user={user} showSettingsLink />
      </div>
    </SidebarFrame>
  )
}

const MAX_VISIBLE_PER_PROJECT = 5

/** 项目子菜单收起过渡时长，需与 CSS 中的 height 过渡保持一致。 */
const SIDEBAR_PROJECT_COLLAPSE_MS = 220

export function ProjectGroup({
  projectName,
  threads,
  activeThreadId,
  onNavigate,
  revealKey,
}: {
  projectName: string
  threads: Array<AgentThread>
  activeThreadId?: string
  onNavigate?: () => void
  revealKey?: string | null
}) {
  const [expanded, setExpanded] = useState(false)
  const hasMore = threads.length > MAX_VISIBLE_PER_PROJECT
  const visibleThreads = expanded || !hasMore ? threads : threads.slice(0, MAX_VISIBLE_PER_PROJECT)

  // 项目子菜单整体收起/展开，默认展开
  const [collapsed, setCollapsed] = useState(false)
  // 收起时先播放退场动画，动画结束后再卸载内容
  const [collapseExiting, setCollapseExiting] = useState(false)
  // 仅用户主动点开的展开才播放入场动画：
  // 侧边栏在「新任务 / 自动化任务 / 技能接口 / 设置」等路由间切换时会重新挂载，
  // 若入场动画无条件跟随挂载，这些跳转都会让项目下的对话导航项无故动一次。
  const [revealOnExpand, setRevealOnExpand] = useState(false)
  const groupContentRef = useRef<HTMLDivElement>(null)
  const collapseTimerRef = useRef<number | null>(null)

  const clearCollapseTimer = () => {
    if (collapseTimerRef.current === null) return
    window.clearTimeout(collapseTimerRef.current)
    collapseTimerRef.current = null
  }

  useEffect(() => clearCollapseTimer, [])

  const toggleCollapsed = () => {
    if (collapsed) {
      // 展开：内容挂回，并标记为「用户主动展开」以播放入场动画
      clearCollapseTimer()
      setCollapseExiting(false)
      setRevealOnExpand(true)
      setCollapsed(false)
      return
    }
    // 收起：先进入退场过渡，过渡结束后再卸载，避免内容瞬间消失
    clearCollapseTimer()
    setRevealOnExpand(false)
    setCollapseExiting(true)
    setCollapsed(true)
    collapseTimerRef.current = window.setTimeout(() => {
      setCollapseExiting(false)
      collapseTimerRef.current = null
    }, SIDEBAR_PROJECT_COLLAPSE_MS)
  }

  // 收起动画：从当前高度过渡到 0，让高度变化平滑而不突兀
  useLayoutEffect(() => {
    if (!collapseExiting) return
    const el = groupContentRef.current
    if (!el) return
    el.style.height = `${el.scrollHeight}px`
    // 强制回流，保证起始高度被应用后再过渡到 0
    void el.offsetHeight
    el.style.height = "0px"
    el.style.opacity = "0"
  }, [collapseExiting])

  // 记录需要动画的线程 ID（仅在展开时新出现的线程）
  const [animatingIds, setAnimatingIds] = useState(new Set<string>())

  const handleExpand = () => {
    if (!expanded && hasMore) {
      // 展开时，标记超出初始可见范围的线程为需要动画
      const newIds = new Set<string>()
      threads.slice(MAX_VISIBLE_PER_PROJECT).forEach((t) => newIds.add(t.id))
      setAnimatingIds(newIds)
    } else {
      setAnimatingIds(new Set())
    }
    setExpanded((v) => !v)
  }

  const navigate = useNavigate()
  const renameProject = useRenameProject()
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameValue, setRenameValue] = useState(projectName)

  const openRename = () => {
    setRenameValue(projectName)
    setRenameOpen(true)
  }

  const confirmRename = () => {
    const next = renameValue.trim()
    if (!next || next === projectName) {
      setRenameOpen(false)
      return
    }
    renameProject.mutate(
      { oldName: projectName, newName: next },
      { onSuccess: () => setRenameOpen(false) }
    )
  }

  // 新建对话：跳转新任务页并预填项目名（本地环境、无本地工作目录）
  const createConversation = () => {
    onNavigate?.()
    navigate({ to: "/agents", search: { project: projectName } })
  }

  return (
    <div className="mb-3">
      {/* 固定行高：避免悬停显示 size-4 操作按钮时撑高标题行，导致上下间距跳动 */}
      <div className="group/proj flex h-6 items-center gap-1.5 px-2 text-[0.625rem] font-semibold tracking-wide text-[var(--ui-text-dim)]">
        {/* 点击项目名称行：收起/展开该项目的对话列表 */}
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          aria-label={collapsed ? `展开项目 ${projectName}` : `收起项目 ${projectName}`}
          title={collapsed ? "展开项目" : "收起项目"}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded px-0.5 py-0.5 text-left transition-colors hover:text-[var(--ui-text-muted)]"
        >
          <CaretDownIcon
            className={cn(
              "size-2.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)]",
              collapsed && "-rotate-90"
            )}
          />
          <FolderOpen className="size-3 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{projectName}</span>
        </button>
        <button
          type="button"
          aria-label="重命名项目"
          title="重命名项目"
          onClick={openRename}
          className="hidden size-4 shrink-0 items-center justify-center rounded hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)] group-hover/proj:flex"
        >
          <Pencil className="size-3" />
        </button>
        <button
          type="button"
          aria-label="新建对话"
          title="新建对话"
          onClick={createConversation}
          className="hidden size-4 shrink-0 items-center justify-center rounded hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text)] group-hover/proj:flex"
        >
          <PlusIcon className="size-3" />
        </button>
      </div>
      {(!collapsed || collapseExiting) && (
        <div
          ref={groupContentRef}
          className={cn(
            // 仅在用户主动展开时播放入场动画，路由切换导致的重新挂载不播
            revealOnExpand && !collapseExiting && "sidebar-project-collapse",
            collapseExiting && "overflow-hidden"
          )}
          style={
            collapseExiting
              ? {
                  transition:
                    "height 220ms cubic-bezier(0.4, 0, 1, 1), opacity 180ms ease-in",
                }
              : undefined
          }
        >
          {visibleThreads.map((thread, idx) => {
            const animate = animatingIds.has(thread.id)
            return (
              <ThreadRow
                key={thread.id}
                thread={thread}
                isActive={thread.id === activeThreadId}
                onNavigate={onNavigate}
                animate={animate}
                animateDelay={animate ? (idx - MAX_VISIBLE_PER_PROJECT) * 40 : undefined}
                revealKey={revealKey}
              />
            )
          })}
          {hasMore && (
            <button
              type="button"
              onClick={handleExpand}
              className="group/expand mx-2 mt-0.5 flex w-[calc(100%-1rem)] items-center justify-center gap-1 rounded-md px-2 py-1 text-[0.6875rem] text-[var(--ui-text-dim)] transition-all duration-200 ease-out hover:bg-[var(--ui-sidebar-hover)] hover:text-[var(--ui-text-muted)] active:scale-[0.97]"
            >
              <CaretDownIcon
                className={cn(
                  "size-3 transition-transform duration-300 ease-[cubic-bezier(0.34,1.56,0.64,1)]",
                  expanded && "rotate-180"
                )}
              />
              <span
                key={expanded ? "collapse" : "expand"}
                className="inline-block animate-[sidebar-expand-text_200ms_ease-out]"
              >
                {expanded ? "收起" : `查看更多 (${threads.length - MAX_VISIBLE_PER_PROJECT})`}
              </span>
            </button>
          )}
        </div>
      )}

      {/* 重命名项目弹窗 */}
      <Dialog.Root open={renameOpen} onOpenChange={setRenameOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-popover shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <Dialog.Title className="text-sm font-semibold text-foreground">
                重命名项目
              </Dialog.Title>
              <Dialog.Close className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                <XIcon className="size-4" />
              </Dialog.Close>
            </div>
            <div className="px-5 py-4">
              <label className="mb-1.5 block text-xs font-medium text-foreground">
                项目名称
              </label>
              <Input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    confirmRename()
                  } else if (e.key === "Escape") {
                    e.preventDefault()
                    setRenameOpen(false)
                  }
                }}
                placeholder="输入新的项目名称"
                autoFocus
              />
              <p className="mt-2 text-xs text-[color:var(--ui-text-dim)]">
                将同步更新该项目下所有对话的归属。
              </p>
            </div>
            <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
              <Button variant="outline" size="sm" onClick={() => setRenameOpen(false)}>
                取消
              </Button>
              <Button
                size="sm"
                disabled={!renameValue.trim() || renameValue.trim() === projectName || renameProject.isPending}
                onClick={confirmRename}
              >
                保存
              </Button>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
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
  animate,
  animateDelay,
  revealKey,
}: {
  thread: AgentThread
  isActive: boolean
  onNavigate?: () => void
  animate?: boolean
  animateDelay?: number
  /** 发送消息后需要播放出现动画的标识（`${threadId}\u0000${seq}`）。 */
  revealKey?: string | null
}) {
  const deleteThread = useDeleteAgentThread()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [exiting, setExiting] = useState(false)
  const badge =
    thread.diffStats && thread.diffStats.additions > 0
      ? `+${thread.diffStats.additions}`
      : null

  const onDelete = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (exiting) return
    setDeleteOpen(true)
  }

  const confirmDelete = () => {
    setDeleteOpen(false)
    setExiting(true)
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
  // 拆分 owner / repo，用于分阶段截断：先省略 owner（保留 /），再省略 repo 尾部
  const repoParts = useMemo(() => {
    const idx = displayRepo.lastIndexOf("/")
    return idx >= 0
      ? { owner: displayRepo.slice(0, idx), repo: displayRepo.slice(idx + 1) }
      : { owner: "", repo: displayRepo }
  }, [displayRepo])
  const envLabel = thread.backendType === "sandbox" ? "沙箱" : "本地"

  // 用户发送消息那一刻标记的导航项出现动画；用 seq 作 key 让重复发送能重播
  const sentRevealSeq = revealKey?.startsWith(`${thread.id}\u0000`)
    ? revealKey.slice(thread.id.length + 1)
    : null
  const playSentReveal = sentRevealSeq !== null && !exiting

  return (
    <>
      <Link
        key={playSentReveal ? sentRevealSeq : undefined}
        to="/agents/$threadId"
        params={{ threadId: thread.id }}
        onClick={onNavigate}
        className={cn(
          "group mb-0.5 flex flex-col gap-0.5 rounded-lg px-2.5 py-1.5",
          isActive
            ? "bg-[var(--ui-accent-bubble)] text-[var(--ui-text)]"
            : "text-[var(--ui-text-muted)] hover:bg-[var(--ui-sidebar-hover)]",
          (animate || playSentReveal) && !exiting && "sidebar-thread-reveal",
          exiting && "sidebar-thread-exit"
        )}
        style={
          exiting
            ? {
                height: 0,
                marginBottom: 0,
                marginTop: 0,
                paddingTop: 0,
                paddingBottom: 0,
                overflow: "hidden",
                opacity: 0,
                transition: "height 200ms ease-in, margin 200ms ease-in, padding 200ms ease-in, opacity 200ms ease-in",
              }
            : animateDelay !== undefined
              ? { animationDelay: `${animateDelay}ms` }
              : undefined
        }
        onAnimationEnd={() => {
          if (exiting) {
            deleteThread.mutate(thread.id)
          }
        }}
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
            {/* 固定行高：避免徽标/删除按钮在悬停切换时撑高行导致间距跳动 */}
            <div className="flex h-4 items-center gap-2">
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
                <span className="shrink-0 rounded bg-[var(--ui-panel-2)] px-1.5 text-[0.625rem] leading-4 text-[var(--ui-text-dim)] group-hover:hidden">
                  {badge}
                </span>
              )}
              <button
                type="button"
                aria-label="删除会话"
                onClick={onDelete}
                disabled={exiting}
                className="hidden size-4 shrink-0 items-center justify-center rounded text-[var(--ui-text-dim)] group-hover:flex hover:bg-[var(--ui-panel-2)] hover:text-destructive"
              >
                <XIcon className="size-3" weight="bold" />
              </button>
            </div>
            {(lastMessageTime || displayRepo) && (
              <div className="flex h-3.5 items-center gap-1 text-[0.5625rem] text-[var(--ui-text-dim)]">
                {lastMessageTime && <span className="shrink-0 whitespace-nowrap">{lastMessageTime}</span>}
                {displayRepo && (
                  <>
                    {lastMessageTime && <span className="shrink-0 opacity-50">·</span>}
                    <span className="flex min-w-0 flex-1 items-center">
                      {repoParts.owner && (
                        <span
                          className="min-w-[1em] truncate"
                          style={{ flexShrink: 10000, direction: "rtl" }}
                        >
                          {repoParts.owner}
                        </span>
                      )}
                      {repoParts.owner && <span className="shrink-0">/</span>}
                      <span className="min-w-0 truncate" style={{ flexShrink: 1 }}>
                        {repoParts.repo}
                      </span>
                    </span>
                  </>
                )}
                {lastMessageTime && <span className="shrink-0 opacity-50">·</span>}
                <span className="shrink-0 whitespace-nowrap">{envLabel}</span>
              </div>
            )}
          </div>
        </div>
      </Link>
      <ConfirmDialog
        open={deleteOpen}
        title="删除会话"
        description={`将永久删除"${thread.title}"及其历史记录，此操作无法撤销。`}
        confirmText="删除"
        destructive
        onOpenChange={setDeleteOpen}
        onConfirm={confirmDelete}
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
