import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Dialog } from "@base-ui/react/dialog"
import {
  CheckCircleIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  UploadSimpleIcon,
  XIcon,
} from "@phosphor-icons/react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { api, type SkillScope } from "@/lib/api"
import { cn } from "@/lib/utils"

/** 工作域短标签，与技能页保持一致。 */
const SCOPE_LABELS: Record<SkillScope, string> = {
  global: "全局",
  project_set: "项目",
  local: "本地",
}

/** 工作域完整说明，用于下拉选项。 */
const SCOPE_OPTIONS: Record<SkillScope, string> = {
  global: "全局（跨项目共享）",
  project_set: "项目（仅当前项目，存于全局目录）",
  local: "本地（仅当前项目，存于项目文件夹）",
}

/**
 * 筛选下拉里可选的工作域。
 *
 * 但这是**上限**，实际列表还要按上下文收缩（见组件内的 `filterScopes`）：
 * 会话没有指定本地工作目录时，列表里只可能来自全局层，把「项目」「本地」
 * 摆在筛选里会让用户以为有得选。
 */
const FILTER_SCOPES = ["global", "project_set", "local"] as const

interface SkillPanelProps {
  open: boolean
  /** 当前会话所属项目名，决定「项目」「本地」两个工作域是否可用。 */
  projectName: string | null
  /**
   * 当前会话的运行环境。沙箱环境下技能目录就是全局目录，不随项目绑定而分层，
   * 因此要与「没指定项目」走同一条分支。
   */
  backendType?: string
  onOpenChange: (open: boolean) => void
}

/**
 * 对话界面里的技能面板。
 *
 * 让用户在任务进行中就能查看当前项目的技能、直接上传新技能，而不必跳出对话
 * 去设置页。面板复用与技能页完全相同的后端接口与校验口径，因此两处看到的
 * 技能列表与作用域可用性永远一致。
 */
export function SkillPanel({
  open,
  projectName,
  backendType,
  onOpenChange,
}: SkillPanelProps) {
  /**
   * 当前会话是否只能看到全局技能。
   *
   * 两种情况等价：没有项目名；或者项目跑在沙箱里——沙箱容器内只有一个技能
   * 目录（对应全局层），`_resolve_skill_sources` 在 `backend_type == "sandbox"`
   * 时也不会反查项目，所以「项目」「本地」两层在沙箱下根本不会加载。
   */
  const globalOnly = !projectName || backendType === "sandbox"

  const queryClient = useQueryClient()
  const skills = useQuery({
    queryKey: ["skills", projectName ?? "*"],
    queryFn: () => api.skills(projectName),
    enabled: open,
  })

  const [query, setQuery] = useState("")
  const [scopeFilter, setScopeFilter] = useState<"all" | SkillScope>("all")
  const [formOpen, setFormOpen] = useState(false)

  const items = skills.data?.items ?? []
  const term = query.trim().toLocaleLowerCase()
  const filtered = items.filter((skill) => {
    if (scopeFilter !== "all" && skill.scope !== scopeFilter) return false
    if (!term) return true
    return `${skill.name} ${skill.description} ${skill.virtualPath}`
      .toLocaleLowerCase()
      .includes(term)
  })

  const invalidate = async () => {
    setFormOpen(false)
    await queryClient.invalidateQueries({ queryKey: ["skills"] })
  }

  /**
   * 筛选下拉里实际可选的项。
   *
   * 未指定本地工作目录（或会话跑在沙箱里）时后端只会返回全局技能，列表里不
   * 存在「项目」「本地」，筛选里也就只留「全局」——多列的选项点了永远是空
   * 结果，是死选项。其余情况恢复成完整三项。
   */
  const filterScopes: readonly SkillScope[] = globalOnly ? ["global"] : FILTER_SCOPES

  /**
   * 上传对话框里可选的工作域。
   *
   * 与筛选下拉**同源**，都由上面 `globalOnly` 这一个条件决定：只能看到全局
   * 时只给「全局」，否则跟随后端 `writableScopes`。前端不叠加额外判断，否则
   * 两处条件稍有不一致就会出现「明明选了项目却只给全局」。
   */
  const writableScopes = skills.data?.writableScopes ?? ["global"]
  const uploadScopes = globalOnly ? (["global"] as SkillScope[]) : writableScopes

  return (
    <>
      <Dialog.Root open={open} onOpenChange={onOpenChange}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
          <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 flex max-h-[min(38rem,calc(100vh-3rem))] w-[min(38rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-popover text-popover-foreground shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="text-sm font-medium">
                  技能
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-xs text-muted-foreground">
                  {globalOnly
                    ? "当前会话未指定本地工作目录，仅展示全局技能。"
                    : `当前项目「${projectName}」可加载的技能，可在此直接上传。`}
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="关闭"
                className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <XIcon className="size-4" />
              </Dialog.Close>
            </header>

            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-5 py-3">
              <div className="relative min-w-0 flex-1">
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
                onValueChange={(next) => setScopeFilter(next as "all" | SkillScope)}
              >
                <SelectTrigger size="sm" className="w-[5.5rem] bg-background">
                  <span className="flex flex-1 text-left">
                    {scopeFilter === "all" ? "全部" : SCOPE_LABELS[scopeFilter]}
                  </span>
                </SelectTrigger>
                <SelectContent align="end" alignItemWithTrigger={false}>
                  <SelectItem value="all">全部</SelectItem>
                  {filterScopes.map((value) => (
                    <SelectItem key={value} value={value}>
                      {SCOPE_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button size="sm" onClick={() => setFormOpen(true)}>
                <PlusIcon className="size-3.5" />
                上传技能
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {skills.isLoading ? (
                <p className="px-5 py-8 text-center text-xs text-muted-foreground">
                  正在读取技能…
                </p>
              ) : skills.isError ? (
                <p className="px-5 py-8 text-center text-xs text-muted-foreground">
                  无法读取技能目录，请确认后端服务可用。
                </p>
              ) : filtered.length === 0 ? (
                <p className="surface-state-enter px-5 py-8 text-center text-xs text-muted-foreground">
                  {query || scopeFilter !== "all"
                    ? "没有匹配的技能。"
                    : "还没有技能，点击「上传技能」添加一个。"}
                </p>
              ) : (
                <ul
                  key={filtered.map((skill) => `${skill.scope}:${skill.id}`).join(":")}
                  className="skill-results divide-y divide-border"
                >
                  {filtered.map((skill, index) => (
                    <li
                      key={`${skill.scope}:${skill.id}`}
                      className="skill-row px-5 py-3"
                      style={{ animationDelay: `${Math.min(index * 30, 150)}ms` }}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-xs font-medium">{skill.name}</p>
                        <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
                          {skill.scopeLabel}
                          {skill.projectName ? ` · ${skill.projectName}` : ""}
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {skill.description}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      <SkillUploadDialog
        open={formOpen}
        projectName={projectName}
        writableScopes={uploadScopes}
        onOpenChange={setFormOpen}
        onDone={invalidate}
      />
    </>
  )
}

interface SkillUploadDialogProps {
  open: boolean
  projectName: string | null
  /** 当前会话真正可写的工作域；未指定本地工作目录时只有「全局」。 */
  writableScopes: Array<SkillScope>
  onOpenChange: (open: boolean) => void
  onDone: () => Promise<void> | void
}

/**
 * 技能上传对话框。
 *
 * 与技能页的创建表单同源，但精简为「上传 / 粘贴」两种模式的单层结构，
 * 适配对话界面里的快速添加场景。
 */
function SkillUploadDialog({
  open,
  projectName,
  writableScopes,
  onOpenChange,
  onDone,
}: SkillUploadDialogProps) {
  const [scope, setScope] = useState<SkillScope>(
    projectName ? "project_set" : "global"
  )
  const [mode, setMode] = useState<"upload" | "paste">("upload")
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [body, setBody] = useState("")
  const [overwrite, setOverwrite] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reset = () => {
    setFile(null)
    setName("")
    setDescription("")
    setBody("")
    setOverwrite(false)
    setMessage(null)
    setError(null)
    setMode("upload")
    setScope(projectName ? "project_set" : "global")
  }

  // 可写工作域只有一个时直接用那个，避免把用户带到不可用的作用域上。
  const effectiveScope = writableScopes.includes(scope)
    ? scope
    : (writableScopes[0] ?? "global")

  const submitMutation = useMutation({
    mutationFn: async () => {
      const projectArg = effectiveScope === "global" ? null : projectName
      if (mode === "upload") {
        if (!file) throw new Error("请先选择 SKILL.md 或 ZIP 技能包")
        const result = await api.uploadSkill(file, {
          scope: effectiveScope,
          projectName: projectArg,
          overwrite,
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
        scope: effectiveScope,
        project_name: projectArg,
      })
      return `已创建技能「${name}」，下次构建智能体时生效`
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

  const canSubmit =
    mode === "upload"
      ? Boolean(file)
      : Boolean(name.trim() && description.trim() && body.trim())

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-[60] bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-[60] w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-popover p-5 text-popover-foreground shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-sm font-medium">上传技能</Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-muted-foreground">
                内容会按技能规范校验，不合格会被拒绝。
              </Dialog.Description>
            </div>
            <div className="flex shrink-0 gap-1 rounded-md border border-border bg-muted p-0.5">
              {(["upload", "paste"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={cn(
                    "rounded px-2 py-1 text-xs transition-colors",
                    mode === value
                      ? "bg-background font-medium shadow-sm"
                      : "text-muted-foreground"
                  )}
                  onClick={() => setMode(value)}
                >
                  {value === "upload" ? "上传文件" : "粘贴创建"}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="panel-skill-scope">工作域</Label>
              <Select
                value={effectiveScope}
                onValueChange={(next) => setScope(next as SkillScope)}
              >
                <SelectTrigger
                  id="panel-skill-scope"
                  size="sm"
                  className="w-full bg-background"
                >
                  <span className="flex flex-1 text-left">
                    {SCOPE_LABELS[effectiveScope]}
                    {effectiveScope !== "global" && projectName
                      ? ` · ${projectName}`
                      : ""}
                  </span>
                </SelectTrigger>
                <SelectContent align="start" alignItemWithTrigger={false}>
                  {/*
                    只列出当前真正可写的工作域。未绑定本地文件夹时「本地」不可用，
                    不显示它比显示成灰色更清楚——避免用户以为功能坏了。
                  */}
                  {writableScopes.map((value) => (
                    <SelectItem key={value} value={value}>
                      {SCOPE_OPTIONS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {writableScopes.length === 1 && (
                <p className="text-[0.625rem] text-muted-foreground">
                  当前会话未指定本地工作目录，只能保存到全局工作域。
                </p>
              )}
              {writableScopes.length === 2 && (
                <p className="text-[0.625rem] text-muted-foreground">
                  项目尚未绑定本地文件夹，暂无「本地」工作域。
                </p>
              )}
            </div>

            {mode === "upload" ? (
              <>
                <label className="flex cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-input bg-input/20 px-3 py-4 text-xs text-muted-foreground transition-colors hover:border-ring">
                  <UploadSimpleIcon className="size-3.5" />
                  <span className="truncate">
                    {file ? file.name : "选择 .md 或 .zip 技能包"}
                  </span>
                  <input
                    type="file"
                    accept=".md,.markdown,.zip"
                    className="sr-only"
                    onChange={(event) =>
                      setFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(event) => setOverwrite(event.target.checked)}
                  />
                  同名技能已存在时覆盖
                </label>
              </>
            ) : (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="panel-skill-name">技能名</Label>
                  <Input
                    id="panel-skill-name"
                    value={name}
                    placeholder="例如 db-migration-check"
                    onChange={(event) => setName(event.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="panel-skill-description">技能说明</Label>
                  <Textarea
                    id="panel-skill-description"
                    value={description}
                    rows={2}
                    maxLength={1024}
                    placeholder="写清什么时候该用这个技能"
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="panel-skill-body">正文</Label>
                  <Textarea
                    id="panel-skill-body"
                    value={body}
                    rows={6}
                    className="font-mono text-xs/relaxed"
                    placeholder={"# 步骤\n\n1. 先做这件事\n2. 再做那件事"}
                    onChange={(event) => setBody(event.target.value)}
                  />
                </div>
              </>
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
              disabled={submitMutation.isPending || !canSubmit}
              onClick={() => submitMutation.mutate()}
            >
              {submitMutation.isPending
                ? "处理中"
                : mode === "upload"
                  ? "导入技能"
                  : "创建技能"}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
