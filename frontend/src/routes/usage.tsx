import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"

import type {
  ReviewerStatsPayload,
  UsageLeaderboardPeriod,
  UsageLeaderboardRow,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/usage")({
  validateSearch: (search: Record<string, unknown>) => ({
    period: typeof search.period === "string" ? search.period : undefined,
  }),
  component: UsagePage,
})

const PERIOD_LABELS: Record<UsageLeaderboardPeriod, string> = {
  "7d": "最近 7 天",
  "30d": "最近 30 天",
  all: "全部时间",
}

const SEVERITY_LABELS: Record<string, string> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
}

function UsagePage() {
  const session = useSession()
  const period =
    (Route.useSearch().period as UsageLeaderboardPeriod | undefined) ?? "30d"
  const navigate = Route.useNavigate()
  const activePeriod: UsageLeaderboardPeriod = ["7d", "30d", "all"].includes(
    period
  )
    ? period
    : "30d"

  const leaderboard = useQuery({
    queryKey: ["usageLeaderboard", activePeriod],
    queryFn: () => api.usageLeaderboard(activePeriod, 10),
    enabled: !!session.data,
    staleTime: 5 * 60 * 1000,
    refetchInterval: (query) => {
      const data = query.state.data
      return data &&
        (!data.generated_at_ms || !data.reviewer_stats.generated_at_ms)
        ? 2000
        : false
    },
  })
  const usageIsPrecomputing =
    !!leaderboard.data && !leaderboard.data.generated_at_ms
  const reviewerIsPrecomputing =
    !!leaderboard.data && !leaderboard.data.reviewer_stats.generated_at_ms

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-64 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  return (
    <AppShell user={session.data} title="用量">
      <SettingsSection
        title="智能体排行榜"
        description="按智能体代码行数排序，其次按已创建 PR 数和智能体运行次数排序。"
        action={
          <Select
            value={activePeriod}
            onValueChange={(value) =>
              navigate({
                to: "/usage",
                search: { period: value as UsageLeaderboardPeriod },
              })
            }
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(PERIOD_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      >
        {leaderboard.isLoading || usageIsPrecomputing ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : leaderboard.isError ? (
          <p className="p-4 text-xs text-destructive">
            加载用量数据失败：{leaderboard.error.message}
          </p>
        ) : !leaderboard.data?.rows.length ? (
          <div className="p-6 text-center text-sm text-muted-foreground">
            {PERIOD_LABELS[activePeriod].toLowerCase()} 还没有记录 XiheAgent 用量。
          </div>
        ) : (
          <UsageTable
            rows={leaderboard.data.rows}
            totalMembers={leaderboard.data.total_members}
          />
        )}
      </SettingsSection>

      <SettingsSection
        title="审查统计"
        description="代码审查发现的问题，以及用户处理这些问题的频率。"
      >
        {leaderboard.isLoading || reviewerIsPrecomputing ? (
          <div className="grid gap-3 p-4 sm:grid-cols-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : leaderboard.isError ? (
          <p className="p-4 text-xs text-destructive">
            加载审查统计失败：{leaderboard.error.message}
          </p>
        ) : leaderboard.data?.reviewer_stats ? (
          <ReviewerStats stats={leaderboard.data.reviewer_stats} />
        ) : (
          <div className="p-6 text-center text-sm text-muted-foreground">
            {PERIOD_LABELS[activePeriod]}还没有审查统计。
          </div>
        )}
      </SettingsSection>
    </AppShell>
  )
}

function UsageTable({
  rows,
  totalMembers,
}: {
  rows: Array<UsageLeaderboardRow>
  totalMembers: number
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="border-b border-border text-xs text-muted-foreground">
          <tr>
            <th className="w-14 px-4 py-3 text-left font-normal">排名</th>
            <th className="px-2 py-3 text-left font-normal">用户</th>
            <th className="px-2 py-3 text-left font-normal">常用模型</th>
            <th className="px-2 py-3 text-right font-normal">运行次数</th>
            <th className="px-2 py-3 text-right font-normal">已创建 PR</th>
            <th className="px-2 py-3 text-right font-normal">已合并 PR</th>
            <th className="px-4 py-3 text-right font-normal">代码行数</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr
              key={`${row.rank}-${row.user.github_login ?? row.user.email ?? row.user.name}`}
            >
              <td className="px-4 py-3 text-muted-foreground">{row.rank}</td>
              <td className="px-2 py-3">
                <UserCell row={row} />
              </td>
              <td className="max-w-48 truncate px-2 py-3 text-muted-foreground">
                {row.favorite_model}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.agent_runs)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.prs_opened)}
              </td>
              <td className="px-2 py-3 text-right tabular-nums">
                {formatNumber(row.merged_prs)}
              </td>
              <td
                className="px-4 py-3 text-right tabular-nums"
                title={`新增 ${formatNumber(row.additions)} 行，删除 ${formatNumber(row.deletions)} 行`}
              >
                {formatNumber(row.agent_loc)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
        共 {formatNumber(totalMembers)} 位成员，显示前 {Math.min(10, totalMembers)} 位
      </div>
    </div>
  )
}

function ReviewerStats({ stats }: { stats: ReviewerStatsPayload }) {
  const cards = [
    {
      label: "已审查 PR",
      value: stats.reviewed_prs,
      helper: `${formatNumber(stats.prs_with_findings)} 个发现问题`,
    },
    {
      label: "发现的问题",
      value: stats.surfaced_findings,
      helper: `已记录 ${formatNumber(stats.findings_recorded)} 个`,
    },
    {
      label: "已处理并解决",
      value: stats.addressed_findings,
      helper: `占已发现问题的 ${formatPercent(stats.resolution_rate)}`,
    },
    {
      label: "更新后解决",
      value: stats.resolved_after_update,
      helper: "在后续 PR 提交中解决",
    },
    {
      label: "等待跟进",
      value: stats.unresolved_surfaced_findings,
      helper: "尚未解决或忽略",
    },
    {
      label: "已忽略",
      value: stats.dismissed_findings,
      helper: `已跟踪 ${formatNumber(stats.human_replies)} 条人工回复`,
    },
  ]

  return (
    <div className="space-y-4 p-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <div key={card.label} className="rounded-md border border-border p-3">
            <div className="text-xs text-muted-foreground">{card.label}</div>
            <div className="mt-1 text-xl font-medium tabular-nums">
              {formatNumber(card.value)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {card.helper}
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2">
        <CounterList title="主要类别" rows={stats.top_categories} />
        <CounterList title="严重程度" rows={severityRows(stats)} />
      </div>
    </div>
  )
}

function severityRows(
  stats: ReviewerStatsPayload
): Array<{ name: string; count: number }> {
  return ["critical", "high", "medium", "low"]
    .map((severity) => ({
      name: SEVERITY_LABELS[severity] ?? severity,
      count: stats.severity_counts[severity] ?? 0,
    }))
    .filter((row) => row.count > 0)
}

function CounterList({
  title,
  rows,
}: {
  title: string
  rows: Array<{ name: string; count: number }>
}) {
  return (
    <div>
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {rows.length ? (
        <ul className="mt-2 space-y-2 text-sm">
          {rows.map((row) => (
            <li
              key={row.name}
              className="flex items-center justify-between gap-3"
            >
              <span className="truncate text-foreground">{row.name}</span>
              <span className="text-muted-foreground tabular-nums">
                {formatNumber(row.count)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">暂无数据。</p>
      )}
    </div>
  )
}

function UserCell({ row }: { row: UsageLeaderboardRow }) {
  const initials = initialsFor(row.user.name)
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Avatar>
        <AvatarFallback>{initials}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-foreground">
          {row.user.name}
        </span>
        <span className="truncate text-xs text-muted-foreground">
          {row.user.email ?? row.user.github_login ?? "未知用户"}
        </span>
      </div>
    </div>
  )
}

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return "?"
  const first = parts[0] ?? "?"
  const second = parts[1]
  if (!second) return first.slice(0, 2).toUpperCase()
  return `${first[0] ?? ""}${second[0] ?? ""}`.toUpperCase()
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value)
}

function formatPercent(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 0,
    style: "percent",
  }).format(value)
}
