import { Navigate, createFileRoute } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import {
  BookOpenTextIcon,
  MagnifyingGlassIcon,
  PlugsConnectedIcon,
  WrenchIcon,
} from "@phosphor-icons/react"
import { useMemo, useState } from "react"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"

export const Route = createFileRoute("/skills")({
  component: SkillsPage,
})

function SkillsPage() {
  const session = useSession()
  if (session.isLoading)
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  if (!session.data) return <Navigate to="/login" />
  return (
    <AppShell
      user={session.data}
      title="技能接口"
      description="查看智能体可按任务需要加载的本地技能与调用入口。"
    >
      <SkillsDirectory />
    </AppShell>
  )
}

function SkillsDirectory() {
  const skills = useQuery({ queryKey: ["skills"], queryFn: api.skills })
  const [query, setQuery] = useState("")
  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase()
    const items = skills.data?.items ?? []
    if (!term) return items
    return items.filter((skill) =>
      `${skill.name} ${skill.description} ${skill.virtualPath}`
        .toLocaleLowerCase()
        .includes(term)
    )
  }, [query, skills.data?.items])

  return (
    <>
      <SettingsSection
        title="已发现技能"
        description={
          skills.data
            ? `${skills.data.items.length} 个技能可用`
            : "正在读取工作区"
        }
        action={
          <div className="relative w-44 sm:w-52">
            <MagnifyingGlassIcon className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="搜索技能"
              className="pl-7"
              value={query}
              placeholder="搜索技能"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        }
        contentClassName="overflow-hidden rounded-lg"
      >
        {skills.isLoading ? (
          <div className="space-y-3 p-4">
            {[0, 1, 2].map((item) => (
              <Skeleton key={item} className="h-20 w-full" />
            ))}
          </div>
        ) : skills.isError ? (
          <EmptyState
            icon={<PlugsConnectedIcon className="size-5" />}
            title="无法读取技能目录"
            description="请确认后端服务和工作区配置可用。"
            action={
              <Button
                size="sm"
                variant="outline"
                onClick={() => skills.refetch()}
              >
                重试
              </Button>
            }
          />
        ) : !skills.data?.available ? (
          <EmptyState
            icon={<WrenchIcon className="size-5" />}
            title="技能目录尚未配置"
            description="设置 AI_WORKSPACE_ROOT，并在 skills 子目录中添加技能后即可在这里查看。"
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<MagnifyingGlassIcon className="size-5" />}
            title={query ? "没有匹配的技能" : "技能目录为空"}
            description={
              query
                ? "尝试使用名称、说明或虚拟路径搜索。"
                : "在技能目录中添加包含 SKILL.md 的子目录。"
            }
          />
        ) : (
          <div className="divide-y divide-border">
            {filtered.map((skill) => (
              <article key={skill.id} className="flex gap-3 px-4 py-3.5">
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <WrenchIcon className="size-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-xs font-medium">{skill.name}</h3>
                    <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
                      可用
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {skill.description}
                  </p>
                  <div className="mt-2 flex items-center gap-1.5 text-[0.625rem] text-muted-foreground">
                    <BookOpenTextIcon className="size-3" />
                    <code className="truncate font-mono">
                      {skill.virtualPath}
                    </code>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </SettingsSection>

      <SettingsSection
        title="调用约定"
        contentClassName="overflow-hidden rounded-lg"
      >
        <div className="grid gap-px bg-border sm:grid-cols-2">
          <div className="bg-card p-4">
            <p className="text-xs font-medium">自动发现</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              智能体启动时读取技能元数据，并根据任务内容选择需要的技能。
            </p>
          </div>
          <div className="bg-card p-4">
            <p className="text-xs font-medium">显式调用</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              在任务指令中写明技能名称，可要求智能体优先使用指定技能。
            </p>
          </div>
        </div>
      </SettingsSection>
    </>
  )
}

function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-3 text-muted-foreground">{icon}</div>
      <p className="text-xs font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
        {description}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
