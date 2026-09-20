import { createFileRoute } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CheckCircleIcon,
  FolderOpenIcon,
  MagnifyingGlassIcon,
  PlugsConnectedIcon,
  PlusIcon,
  UploadSimpleIcon,
  WrenchIcon,
} from "@phosphor-icons/react"
import { useMemo, useRef, useState } from "react"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { SkillRow } from "@/components/skills/SkillRow"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { api, type AgentSkill, type SkillScope } from "@/lib/api"
import { useAgentThreads } from "@/lib/agents/queries"
import { useSession } from "@/lib/session"
import { cn, validatePath } from "@/lib/utils"

export const Route = createFileRoute("/skills")({
  component: SkillsPage,
})

/** 作用域筛选下拉与文案里使用的短标签。 */
const SCOPE_FILTER_LABELS: Record<SkillScope, string> = {
  global: "全局",
  project_set: "项目",
  local: "本地",
}

/** 创建表单里各作用域的完整说明，用于下拉选项。 */
const SCOPE_OPTION_LABELS: Record<SkillScope, string> = {
  global: "全局（跨项目共享）",
  project_set: "项目（仅当前项目，存于全局目录）",
  local: "本地（仅当前项目，存于项目文件夹）",
}

/** 按路径风格推断分隔符，用于拼出展示用的完整落点路径。 */
function pathSeparatorFor(rawPath: string): string {
  return rawPath.includes("\\") ? "\\" : "/"
}

/** 拼出某个文件夹下本地技能的落点，末尾不带分隔符。 */
function localSkillsPathIn(folder: string): string {
  const trimmed = folder.trim().replace(/[\\/]+$/, "")
  if (!trimmed) return ""
  const sep = pathSeparatorFor(trimmed)
  return `${trimmed}${sep}.xihe-agent${sep}skills`
}

function SkillsPage() {
  const session = useSession()
  if (session.isLoading)
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  if (!session.data) {
    return (
      <main className="p-6">
        <p className="text-sm text-muted-foreground">读取会话信息失败，请刷新页面重试。</p>
      </main>
    )
  }
  return (
    <AppShell
      user={session.data}
      title="技能接口"
      description="管理智能体可按任务需要加载的本地技能，区分全局与项目作用域。"
    >
      <SkillsDirectory />
    </AppShell>
  )
}

function SkillsDirectory() {
  const queryClient = useQueryClient()
  const [projectName, setProjectName] = useState<string | null>(null)
  /**
   * 项目列表来自对话所属的项目名，与侧边栏的分组口径完全一致。
   *
   * 不能改用 `/workspace/projects`：那个接口扫的是工作区下 clone 出来的仓库
   * 目录，跟「用户创建的项目集合」不是一回事，会把 `xihe-agent-test` 这类仓库
   * 目录也列进来。
   */
  const threadsQuery = useAgentThreads()
  const projectNames = useMemo(() => {
    const names = new Set<string>()
    for (const thread of threadsQuery.data ?? []) {
      const name = thread.projectName?.trim()
      if (name) names.add(name)
    }
    return [...names].sort((a, b) => a.localeCompare(b))
  }, [threadsQuery.data])
  const skillQueryKey = ["skills", projectName ?? "*"]
  const skills = useQuery({
    queryKey: skillQueryKey,
    queryFn: () => api.skills(projectName),
  })
  /**
   * 项目的本地文件夹绑定，用于在「本地」作用域里预填已绑定的目录。
   *
   * 技能页本身不展示绑定关系，但创建本地技能时必须知道它——否则已绑定的项目
   * 会显示一个空文件夹框，用户可能误以为没绑定过而重新选一个目录。
   */
  const projectFolders = useQuery({
    queryKey: ["workspace-projects"],
    queryFn: api.listProjects,
  })

  const [query, setQuery] = useState("")
  const [scopeFilter, setScopeFilter] = useState<"all" | SkillScope>("all")
  const [formOpen, setFormOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<AgentSkill | null>(null)

  const items = skills.data?.items ?? []
  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase()
    return items.filter((skill) => {
      if (scopeFilter !== "all" && skill.scope !== scopeFilter) return false
      if (!term) return true
      return `${skill.name} ${skill.description} ${skill.virtualPath}`
        .toLocaleLowerCase()
        .includes(term)
    })
  }, [items, query, scopeFilter])

  const deleteMutation = useMutation({
    mutationFn: (skill: AgentSkill) =>
      api.deleteSkill(skill.id, skill.scope, skill.projectName),
    onSuccess: async () => {
      setPendingDelete(null)
      await queryClient.invalidateQueries({ queryKey: ["skills"] })
    },
  })

  const invalidate = async () => {
    setFormOpen(false)
    await queryClient.invalidateQueries({ queryKey: ["skills"] })
  }

  /**
   * 作用域卡片只展示「全局」与「项目」两张。
   *
   * 「本地」作用域仍在后端正常运作（项目绑定文件夹后落盘到 `.xihe-agent/skills`，
   * 智能体侧也依赖它加载），只是不在这个概览区展示——它本质上依附于某个具体项目，
   * 与另外两个跨项目的层不是一个维度，单列一张卡反而让人以为需要单独配置。
   * 下拉里的作用域选项与创建表单不受影响。
   */
  const cards = useMemo(
    () => (skills.data?.scopes ?? []).filter((scope) => scope.scope !== "local"),
    [skills.data]
  )
  /** 已绑定本地文件夹的项目：项目名 → 文件夹绝对路径。 */
  const boundFolders = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of projectFolders.data?.projects ?? []) {
      if (item.local_folder) map.set(item.name, item.local_folder)
    }
    return map
  }, [projectFolders.data])
  const countByScope = (scope: SkillScope) =>
    items.filter((item) => item.scope === scope).length

  /**
   * 筛选下拉里可选的作用域。
   *
   * 固定列出全部三项，不随「是否指定项目」收缩——筛选是对**已发现技能**的过滤，
   * 未指定项目时列表里仍可能有项目或本地技能，把选项藏起来会让用户以为列表里
   * 只有全局技能。
   */
  const filterScopes = useMemo(() => ["global", "project_set", "local"] as const, [])

  return (
    <>
      <SettingsSection
        title="作用域"
        description={
          projectName
            ? `全局技能跨项目共享；项目与本地技能仅在「${projectName}」内生效，同名时逐层覆盖。`
            : projectNames.length > 0
              ? "当前展示所有项目的技能，在右上角选择项目可聚焦到该项目的全局、项目与本地技能。"
              : "暂无可用项目。创建带项目的对话后，这里就能选择项目作用域。"
        }
        action={
          <Select
            value={projectName ?? "__none__"}
            onValueChange={(next) =>
              setProjectName(next === "__none__" ? null : next)
            }
          >
            <SelectTrigger size="sm" className="w-[11rem] bg-background">
              <span className="flex flex-1 truncate text-left">
                {projectName ?? "不指定项目"}
              </span>
            </SelectTrigger>
            <SelectContent align="end" alignItemWithTrigger={false}>
              <SelectItem value="__none__">不指定项目</SelectItem>
              {projectNames.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        contentClassName="overflow-hidden rounded-lg"
      >
        <div className="grid gap-px bg-border sm:grid-cols-2">
          {cards.map((scope) => (
            <div key={scope.scope} className="bg-card p-4">
              <div className="flex items-center gap-2">
                <p className="text-xs font-medium">{scope.label}作用域</p>
                <span
                  className={cn(
                    "rounded-sm border px-1.5 py-0.5 text-[0.625rem]",
                    scope.available
                      ? "border-border bg-muted text-muted-foreground"
                      : "border-destructive/30 bg-destructive/10 text-destructive"
                  )}
                >
                  {scope.available ? "可用" : "不可用"}
                </span>
                <span className="text-[0.625rem] text-muted-foreground">
                  {countByScope(scope.scope)} 个
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {scope.description}
              </p>
              {scope.directory && (
                <code className="mt-2 block truncate font-mono text-[0.625rem] text-muted-foreground">
                  {scope.directory}
                </code>
              )}
            </div>
          ))}
          {cards.length === 0 && (
            <div className="bg-card p-4 text-xs text-muted-foreground sm:col-span-2">
              正在读取作用域配置
            </div>
          )}
        </div>
      </SettingsSection>

      <SettingsSection
        title="已发现技能"
        description={
          skills.data
            ? `共 ${items.length} 个技能${scopeFilter === "all" ? "" : `，筛选后 ${filtered.length} 个`}`
            : "正在读取技能目录"
        }
        action={
          <div className="flex items-center gap-2">
            <div className="relative w-36 sm:w-44">
              <MagnifyingGlassIcon className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="搜索技能"
                className="pl-7"
                value={query}
                placeholder="搜索技能"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <Select
              value={scopeFilter}
              onValueChange={(next) =>
                setScopeFilter(next as "all" | SkillScope)
              }
            >
              <SelectTrigger size="sm" className="w-[5.5rem] bg-background">
                <span className="flex flex-1 text-left">
                  {scopeFilter === "all" ? "全部" : SCOPE_FILTER_LABELS[scopeFilter]}
                </span>
              </SelectTrigger>
              <SelectContent align="end" alignItemWithTrigger={false}>
                <SelectItem value="all">全部</SelectItem>
                {filterScopes.map((value) => (
                  <SelectItem key={value} value={value}>
                    {SCOPE_FILTER_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" onClick={() => setFormOpen(true)}>
              <PlusIcon className="size-3.5" />
              新建技能
            </Button>
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
            description="请在设置中确认工作目录，技能会安装到该目录下的 skills 子目录。"
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<MagnifyingGlassIcon className="size-5" />}
            title={
              query || scopeFilter !== "all"
                ? "没有匹配的技能"
                : "技能目录为空"
            }
            description={
              query || scopeFilter !== "all"
                ? "尝试使用名称、说明或虚拟路径搜索，或切换作用域筛选。"
                : "点击「新建技能」粘贴内容，或上传 SKILL.md / ZIP 技能包。"
            }
          />
        ) : (
          <div
            key={filtered.map((skill) => `${skill.scope}:${skill.id}`).join(":")}
            className="skill-results divide-y divide-border"
          >
            {filtered.map((skill, index) => (
              <SkillRow
                key={`${skill.scope}:${skill.id}`}
                skill={skill}
                index={index}
                onDelete={() => setPendingDelete(skill)}
              />
            ))}
          </div>
        )}
      </SettingsSection>

      <SkillFormDialog
        open={formOpen}
        projectNames={projectNames}
        boundFolders={boundFolders}
        onOpenChange={setFormOpen}
        onDone={invalidate}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除技能"
        description={
          pendingDelete
            ? `将删除技能「${pendingDelete.name}」（${pendingDelete.scopeLabel}作用域）及其全部附属文件。此操作不可撤销。`
            : ""
        }
        confirmText="删除"
        destructive
        busy={deleteMutation.isPending}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null)
        }}
        onConfirm={() => {
          if (pendingDelete) deleteMutation.mutate(pendingDelete)
        }}
      />
    </>
  )
}

function SkillFormDialog({
  open,
  projectNames,
  boundFolders,
  onOpenChange,
  onDone,
}: {
  open: boolean
  /** 可选的项目名列表，供「项目」作用域选择归属。 */
  projectNames: Array<string>
  /** 已绑定本地文件夹的项目，用于预填「本地」作用域的目录。 */
  boundFolders: Map<string, string>
  onOpenChange: (open: boolean) => void
  onDone: () => Promise<void> | void
}) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<"paste" | "upload">("paste")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [body, setBody] = useState("")
  const [scope, setScope] = useState<SkillScope>("global")
  /**
   * 「项目」作用域的归属项目。
   *
   * 与顶部项目选择器完全独立：顶部那个决定「列出哪些项目的技能」，
   * 这里决定「这个新技能存到哪个项目」。
   */
  const [targetProject, setTargetProject] = useState<string>(
    projectNames[0] ?? ""
  )
  /**
   * 「本地」作用域的目标文件夹。
   *
   * 后端是按**项目名**反查绑定文件夹来定位本地技能目录的
   * （`resolve_project_folder` → store 里的 `local_folder`），并不接受直接传路径。
   * 所以这里选中的文件夹会在提交前先绑定到 `targetProject`，再以 `local` 作用域创建。
   */
  const [localFolder, setLocalFolder] = useState(
    () => boundFolders.get(projectNames[0] ?? "") ?? ""
  )
  const [picking, setPicking] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const reset = () => {
    setName("")
    setDescription("")
    setBody("")
    setOverwrite(false)
    setFile(null)
    setMessage(null)
    setError(null)
    setMode("paste")
    setScope("global")
    setLocalFolder(
      boundFolders.get(projectNames[0] ?? "") ?? ""
    )
    setTargetProject(projectNames[0] ?? "")
  }

  /** 切换归属项目时，若它已绑定文件夹就预填，避免用户误以为没绑定。 */
  const handleTargetProjectChange = (next: string | null) => {
    const name = !next || next === "__none__" ? "" : next
    setTargetProject(name)
    setLocalFolder(name ? (boundFolders.get(name) ?? "") : "")
  }

  const folderValidation = useMemo(
    () =>
      localFolder.trim()
        ? validatePath(localFolder)
        : { valid: true, error: "", platform: "unknown" as const },
    [localFolder]
  )

  /** 本地技能的实际落点，用于提示文案。 */
  const localSkillDir = localSkillsPathIn(localFolder)

  const handlePickFolder = async () => {
    setPicking(true)
    try {
      const result = await api.pickFolder()
      if (result.path) setLocalFolder(result.path)
    } catch {
      // 用户取消或系统对话框不可用
    } finally {
      setPicking(false)
    }
  }

  /** 按当前模式真正写入技能；`projectArg` 为 `null` 表示全局作用域。 */
  const createOrUpload = async (
    projectArg: string | null,
    localFolderArg?: string | null
  ): Promise<string> => {
    if (mode === "upload") {
      if (!file) throw new Error("请先选择 SKILL.md 或 ZIP 技能包")
      const result = await api.uploadSkill(file, {
        scope,
        projectName: projectArg,
        overwrite,
        localFolder: localFolderArg,
      })
      const [firstFailed] = result.failed
      const failedNote = firstFailed
        ? `，${result.failed.length} 个条目被跳过（${firstFailed.reason}）`
        : ""
      return `已导入 ${result.created.length} 个技能${failedNote}`
    }
    await api.createSkill({
      name,
      description,
      body,
      scope,
      project_name: projectArg,
      local_folder: localFolderArg,
    })
    return `已创建技能「${name}」，下次构建智能体时生效`
  }

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (scope === "global") {
        return createOrUpload(null)
      }

      if (scope === "local") {
        const folder = localFolder.trim()
        if (!folder) throw new Error("请先选择本地文件夹")
        if (!folderValidation.valid) throw new Error(folderValidation.error)

        const project = targetProject.trim()
        if (project) {
          await api.bindProjectFolder({ project_name: project, local_folder: folder })
          await queryClient.invalidateQueries({ queryKey: ["workspace-projects"] })
          return createOrUpload(project)
        }
        return createOrUpload(null, folder)
      }

      const project = targetProject.trim()
      if (!project) throw new Error("请选择归属项目")
      return createOrUpload(project)
    },
    onSuccess: (note) => {
      setError(null)
      setMessage(note)
      window.setTimeout(() => {
        reset()
        void onDone()
      }, 900)
    },
    onError: (err: unknown) => {
      setMessage(null)
      setError(err instanceof Error ? err.message : "操作失败，请重试")
    },
  })

  if (!open) return null

  /**
   * 作用域下拉永远列出全部三项，不随上下文收缩。
   *
   * 这一页可以在右上角自由切换「不指定项目 / 具体项目」，表单里的作用域又是
   * 一个独立的意图表达（「我要建到哪一层」），把选项藏起来只会让人以为该功能
   * 不存在。选到当前不可用的层时，由下方的归属项目/本地目录字段引导补齐条件。
   */
  const scopeChoices: readonly SkillScope[] = ["global", "project_set", "local"]
  const effectiveScope = scope

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="新建技能"
        className="w-full max-w-2xl rounded-lg border border-border bg-popover p-5 text-popover-foreground shadow-xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-medium">新建技能</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              内容会按技能规范校验，不合格会被拒绝，避免产生智能体加载不到的技能。
            </p>
          </div>
          <div className="flex shrink-0 gap-1 rounded-md border border-border bg-muted p-0.5">
            <button
              type="button"
              className={cn(
                "rounded px-2 py-1 text-xs transition-colors",
                mode === "paste"
                  ? "bg-background font-medium shadow-sm"
                  : "text-muted-foreground"
              )}
              onClick={() => setMode("paste")}
            >
              粘贴创建
            </button>
            <button
              type="button"
              className={cn(
                "rounded px-2 py-1 text-xs transition-colors",
                mode === "upload"
                  ? "bg-background font-medium shadow-sm"
                  : "text-muted-foreground"
              )}
              onClick={() => setMode("upload")}
            >
              上传文件
            </button>
          </div>
        </div>

        <div className="mt-4 space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="skill-scope">作用域</Label>
              <Select
                value={effectiveScope}
                onValueChange={(next) => setScope(next as SkillScope)}
              >
                <SelectTrigger
                  id="skill-scope"
                  size="sm"
                  className="w-full bg-background"
                >
                  <span className="flex flex-1 text-left">
                    {SCOPE_FILTER_LABELS[effectiveScope]}
                  </span>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {scopeChoices.map((value) => (
                    <SelectItem key={value} value={value}>
                      {SCOPE_OPTION_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {mode === "upload" && (
              <div className="flex items-end">
                <label className="flex w-full cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-input bg-input/20 px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-ring">
                  <UploadSimpleIcon className="size-3.5" />
                  <span className="truncate">
                    {file ? file.name : "选择 .md 或 .zip"}
                  </span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".md,.markdown,.zip"
                    className="sr-only"
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
              </div>
            )}
          </div>

          {/* 作用域决定存放位置，这里按所选作用域补充对应的归属选择。 */}
          {effectiveScope === "project_set" && (
            <div className="space-y-1.5">
              <Label htmlFor="skill-target-project">归属项目</Label>
              <Select
                value={targetProject || "__none__"}
                onValueChange={handleTargetProjectChange}
              >
                <SelectTrigger
                  id="skill-target-project"
                  size="sm"
                  className="w-full bg-background"
                >
                  <span className="flex flex-1 truncate text-left">
                    {targetProject || "请选择项目"}
                  </span>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  <SelectItem value="__none__">请选择项目</SelectItem>
                  {projectNames.map((value) => (
                    <SelectItem key={value} value={value}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[0.625rem] text-muted-foreground">
                {projectNames.length === 0
                  ? "暂无可用项目。创建带项目的对话后，这里就能选择归属项目。"
                  : targetProject
                    ? `将存放到全局技能目录下的 .project-set/${targetProject}/`
                    : "技能会存到全局技能目录下按项目名分隔的目录里。"}
              </p>
            </div>
          )}

          {effectiveScope === "local" && (
            <div className="space-y-1.5">
              <Label htmlFor="skill-local-folder">本地文件夹</Label>
              <div className="flex items-center gap-2">
                <Input
                  id="skill-local-folder"
                  value={localFolder}
                  placeholder="选择文件夹，例如 E:\\my_project\\Xihe"
                  className="min-w-0 flex-1"
                  onChange={(event) => setLocalFolder(event.target.value)}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="shrink-0"
                  disabled={picking}
                  onClick={handlePickFolder}
                >
                  <FolderOpenIcon className="size-3.5" />
                  {picking ? "选择中" : "选择文件夹"}
                </Button>
              </div>
              <p className="text-[0.625rem] text-muted-foreground">
                技能直接写入所选文件夹，建议选择项目根目录——它会存放到项目根目录下的{" "}
                <code className="font-mono">/.xihe-agent/skills</code>。
              </p>
              {localFolder.trim() && !folderValidation.valid && (
                <p className="text-[0.625rem] text-destructive">
                  {folderValidation.error}
                </p>
              )}
              {localSkillDir && folderValidation.valid && (
                <p className="text-[0.625rem] text-muted-foreground">
                  实际存放位置：
                  <code className="ml-1 font-mono break-all">
                    {localSkillDir}
                  </code>
                </p>
              )}
            </div>
          )}

          {mode === "paste" ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="skill-name">技能名</Label>
                <Input
                  id="skill-name"
                  value={name}
                  placeholder="例如 db-migration-check，仅小写字母、数字与连字符"
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="skill-description">技能说明</Label>
                <Textarea
                  id="skill-description"
                  value={description}
                  rows={2}
                  maxLength={1024}
                  placeholder="写清什么时候该用这个技能，这是智能体判断是否加载的唯一依据"
                  onChange={(event) => setDescription(event.target.value)}
                />
                <p className="text-[0.625rem] text-muted-foreground">
                  {description.length} / 1024
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="skill-body">正文</Label>
                <Textarea
                  id="skill-body"
                  value={body}
                  rows={8}
                  className="font-mono text-xs/relaxed"
                  placeholder={"# 步骤\n\n1. 先做这件事\n2. 再做那件事"}
                  onChange={(event) => setBody(event.target.value)}
                />
              </div>
            </>
          ) : (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(event) => setOverwrite(event.target.checked)}
              />
              同名技能已存在时覆盖
            </label>
          )}

          {error && (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}
          {message && (
            <p className="flex items-center gap-2 rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
              <CheckCircleIcon className="size-3.5" />
              {message}
            </p>
          )}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={submitMutation.isPending}
            onClick={() => {
              reset()
              onOpenChange(false)
            }}
          >
            取消
          </Button>
          <Button
            size="sm"
            disabled={
              submitMutation.isPending ||
              (mode === "upload"
                ? !file
                : !name.trim() || !description.trim() || !body.trim()) ||
              // 非全局作用域必须能定位到存放目录，否则提交一定失败
              (effectiveScope === "project_set" && !targetProject.trim()) ||
              // 本地作用域只需要选定文件夹且路径合法
              (effectiveScope === "local" &&
                (!localFolder.trim() ||
                  !folderValidation.valid))
            }
            onClick={() => submitMutation.mutate()}
          >
            {submitMutation.isPending
              ? "处理中"
              : mode === "upload"
                ? "导入技能"
                : "创建技能"}
          </Button>
        </div>
      </div>
    </div>
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
    <div className="surface-state-enter flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-3 text-muted-foreground">{icon}</div>
      <p className="text-xs font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
        {description}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
