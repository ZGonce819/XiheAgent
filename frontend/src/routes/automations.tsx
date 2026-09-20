import { createFileRoute, Link } from "@tanstack/react-router"
import {
  CalendarBlankIcon,
  CaretDownIcon,
  ClockIcon,
  PencilSimpleIcon,
  PlayIcon,
  PlusIcon,
  RobotIcon,
  TrashIcon,
} from "@phosphor-icons/react"
import { useEffect, useState, type ReactNode } from "react"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { Markdown } from "@/components/agents/ported/Markdown"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { formatRelativeTime } from "@/lib/agents/api"
import { CRON_PRESETS, describeCron, presetForCron } from "@/lib/agents/cron"
import {
  useAgentScheduleState,
  useAgentSchedules,
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useResumeAgentSchedule,
  useResetAgentSchedule,
  useRunAgentSchedule,
  useUpdateAgentSchedule,
  useUpdateAgentScheduleBudget,
} from "@/lib/agents/queries"
import type { AgentSchedule, AutomationRunReceipt } from "@/lib/agents/types"
import { useModelOptions } from "@/lib/agents/useModelOptions"
import { useSession } from "@/lib/session"
import { detectRepoProvider, type RepoProvider } from "@/lib/repo"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/automations")({
  component: AutomationsPage,
})

type FormState = {
  name: string
  prompt: string
  schedule: string
  preset: "hourly" | "daily" | "weekly" | "custom"
  repo: string
  platform: RepoProvider
  modelId: string
  autoApprove: boolean
}

const EMPTY_FORM: FormState = {
  name: "",
  prompt: "",
  schedule: "0 9 * * *",
  preset: "daily",
  repo: "",
  platform: "github",
  modelId: "",
  autoApprove: false,
}

function AutomationsPage() {
  const session = useSession()
  if (session.isLoading) return <PageSkeleton />
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
      title="自动化任务"
      description="按计划运行重复任务，并保留每次执行产生的会话记录。"
    >
      <AutomationManager />
    </AppShell>
  )
}

function AutomationManager() {
  const schedules = useAgentSchedules()
  const createSchedule = useCreateAgentSchedule()
  const updateSchedule = useUpdateAgentSchedule()
  const deleteSchedule = useDeleteAgentSchedule()
  const modelOptions = useModelOptions()
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<AgentSchedule | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AgentSchedule | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!form.modelId && modelOptions.defaultSelection) {
      setForm((current) => ({
        ...current,
        modelId: modelOptions.defaultSelection?.modelId ?? "",
      }))
    }
  }, [form.modelId, modelOptions.defaultSelection])

  const resetForm = () => {
    setEditing(null)
    setForm({
      ...EMPTY_FORM,
      modelId: modelOptions.defaultSelection?.modelId ?? "",
    })
    setError(null)
    setFormOpen(false)
  }

  const startEdit = (schedule: AgentSchedule) => {
    setEditing(schedule)
    setForm({
      name: schedule.name,
      prompt: schedule.prompt,
      schedule: schedule.schedule,
      preset: presetForCron(schedule.schedule),
      repo: schedule.repo ?? "",
      platform: detectRepoProvider(schedule.repo ?? "") ?? "github",
      modelId: schedule.model,
      autoApprove: schedule.autoApprove === true,
    })
    setError(null)
    setFormOpen(true)
  }

  const save = async () => {
    if (!form.name.trim() || !form.prompt.trim()) {
      setError("请填写任务名称和任务指令。")
      return
    }
    if (form.schedule.trim().split(/\s+/).length !== 5) {
      setError("Cron 表达式需要包含 5 个字段。")
      return
    }
    setError(null)
    const body = {
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      schedule: form.schedule.trim(),
      repo: form.repo.trim() || null,
      model_id: form.modelId || null,
      autoApprove: form.autoApprove,
    }
    try {
      if (editing) {
        await updateSchedule.mutateAsync({ scheduleId: editing.id, body })
      } else {
        await createSchedule.mutateAsync(body)
      }
      resetForm()
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "保存失败，请稍后重试。"
      )
    }
  }

  const busy = createSchedule.isPending || updateSchedule.isPending
  const items = schedules.data ?? []

  return (
    <>
      <SettingsSection
        title="任务列表"
        description={`${items.length} 个自动化任务`}
        action={
          <Button
            size="sm"
            onClick={() => setFormOpen(true)}
            disabled={formOpen}
          >
            <PlusIcon />
            新建任务
          </Button>
        }
        contentClassName="overflow-hidden rounded-lg"
      >
        {schedules.isLoading ? (
          <div className="space-y-3 p-4">
            {[0, 1, 2].map((item) => (
              <Skeleton key={item} className="h-16 w-full" />
            ))}
          </div>
        ) : schedules.isError ? (
          <StateMessage
            title="无法载入自动化任务"
            description="请确认后端服务已启动，然后重试。"
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => schedules.refetch()}
              >
                重试
              </Button>
            }
          />
        ) : items.length === 0 && !formOpen ? (
          <StateMessage
            icon={<CalendarBlankIcon className="size-5" />}
            title="还没有自动化任务"
            description="创建一个任务，让智能体按固定计划处理重复工作。"
            action={
              <Button size="sm" onClick={() => setFormOpen(true)}>
                <PlusIcon />
                新建任务
              </Button>
            }
          />
        ) : (
          <div className="divide-y divide-border">
            {items.map((item, index) => (
              <AutomationRow
                key={item.id}
                schedule={item}
                index={index}
                disabled={updateSchedule.isPending}
                onEdit={() => startEdit(item)}
                onDelete={() => setDeleteTarget(item)}
                onToggle={(enabled) =>
                  updateSchedule.mutate({
                    scheduleId: item.id,
                    body: { enabled },
                  })
                }
              />
            ))}
          </div>
        )}
      </SettingsSection>

      {formOpen && (
        <div
          key={editing?.id ?? "new-automation"}
          className="automation-form-reveal"
        >
          <SettingsSection
            title={editing ? "编辑任务" : "新建任务"}
            description="时间统一按 UTC 解释，执行结果将出现在会话列表中。"
            contentClassName="rounded-lg"
          >
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="任务名称" htmlFor="automation-name">
                  <Input
                    id="automation-name"
                    value={form.name}
                    placeholder="例如：每日依赖检查"
                    onChange={(event) =>
                      setForm({ ...form, name: event.target.value })
                    }
                  />
                </Field>
                <Field label="代码仓库" htmlFor="automation-repo" hint="可选">
                  <div className="flex gap-2">
                    <Select
                      value={form.platform}
                      onValueChange={(next) =>
                        setForm({ ...form, platform: next as RepoProvider })
                      }
                    >
                      <SelectTrigger
                        id="automation-platform"
                        className="w-28 shrink-0 bg-background"
                      >
                        <span className="flex flex-1 text-left">
                          {form.platform === "github"
                            ? "GitHub"
                            : form.platform === "gitee"
                              ? "Gitee"
                              : "Codeup"}
                        </span>
                      </SelectTrigger>
                      <SelectContent align="start" alignItemWithTrigger={false}>
                        <SelectItem value="github">GitHub</SelectItem>
                        <SelectItem value="gitee">Gitee</SelectItem>
                        <SelectItem value="codeup">Codeup</SelectItem>
                      </SelectContent>
                    </Select>
                    <Input
                      id="automation-repo"
                      value={form.repo}
                      placeholder="owner/repository"
                      onChange={(event) =>
                        setForm({ ...form, repo: event.target.value })
                      }
                    />
                  </div>
                </Field>
              </div>
              <Field label="任务指令" htmlFor="automation-prompt">
                <Textarea
                  id="automation-prompt"
                  className="min-h-24"
                  value={form.prompt}
                  placeholder="清楚描述每次运行需要完成的工作。"
                  onChange={(event) =>
                    setForm({ ...form, prompt: event.target.value })
                  }
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="执行频率" htmlFor="automation-preset">
                  <Select
                    value={form.preset}
                    onValueChange={(next) => {
                      const preset = next as FormState["preset"]
                      const match = CRON_PRESETS.find(
                        (item) => item.id === preset
                      )
                      setForm({
                        ...form,
                        preset,
                        schedule: match?.value ?? form.schedule,
                      })
                    }}
                  >
                    <SelectTrigger
                      id="automation-preset"
                      className="w-full bg-background"
                    >
                      <span className="flex flex-1 text-left">
                        {CRON_PRESETS.find((item) => item.id === form.preset)
                          ?.label ?? "自定义"}
                      </span>
                    </SelectTrigger>
                    {/* 与技能页一致：菜单从触发器下方浮出（带滑入动画），
                        不用默认的「对齐触发器原地展开」——那个模式没有浮层感 */}
                    <SelectContent align="start" alignItemWithTrigger={false}>
                      {CRON_PRESETS.map((preset) => (
                        <SelectItem key={preset.id} value={preset.id}>
                          {preset.label}
                        </SelectItem>
                      ))}
                      <SelectItem value="custom">自定义</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Cron 表达式" htmlFor="automation-cron">
                  <Input
                    id="automation-cron"
                    className="font-mono tabular-nums"
                    value={form.schedule}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        schedule: event.target.value,
                        preset: presetForCron(event.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="模型" htmlFor="automation-model">
                  <Select
                    value={form.modelId}
                    onValueChange={(next) =>
                      setForm({ ...form, modelId: next as string })
                    }
                  >
                    <SelectTrigger
                      id="automation-model"
                      className="w-full bg-background"
                    >
                      <span className="flex flex-1 text-left">
                        {modelOptions.models.find(
                          (model) => model.id === form.modelId
                        )?.label ?? form.modelId}
                      </span>
                    </SelectTrigger>
                    <SelectContent align="start" alignItemWithTrigger={false}>
                      {modelOptions.models.map((model) => (
                        <SelectItem key={model.id} value={model.id}>
                          {model.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>
              <Field
                label="全流程自动化（审批交给表决）"
                htmlFor="automation-auto-approve"
              >
                <div className="flex items-start gap-2 pt-0.5">
                  <Switch
                    id="automation-auto-approve"
                    checked={form.autoApprove}
                    onCheckedChange={(checked) =>
                      setForm({ ...form, autoApprove: checked })
                    }
                  />
                  <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                    开启后，需要审批的命令由三个评审各自从不同角度表决（多数决），
                    不再停下来等人点确认。
                    <br />
                    评审无法形成结论时按安全拒绝处理；按策略硬拒绝的命令永远不放行；
                    一轮最多表决 5 次，用满仍卡住时本轮自动失败，不会转回人工审批。
                  </p>
                </div>
              </Field>
              {error && (
                <p role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              )}
              <div className="flex gap-2 pt-1">
                <Button size="sm" disabled={busy} onClick={save}>
                  {busy ? "保存中..." : editing ? "保存修改" : "创建任务"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={resetForm}
                >
                  取消
                </Button>
              </div>
            </div>
          </SettingsSection>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除自动化任务"
        description={
          deleteTarget
            ? `将永久删除“${deleteTarget.name}”的配置，已有会话记录不会受到影响。`
            : ""
        }
        confirmText="删除"
        destructive
        busy={deleteSchedule.isPending}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        onConfirm={() => {
          if (!deleteTarget) return
          deleteSchedule.mutate(deleteTarget.id, {
            onSuccess: () => setDeleteTarget(null),
          })
        }}
      />
    </>
  )
}

function describeLastTriggered(iso: string): string {
  return `上次触发 ${formatRelativeTime(Date.parse(iso))}前`
}

/** 三个预算上限在表单里是文本，保存时才校验并转成数字。 */
type BudgetDraft = {
  maxRuns: string
  maxTokens: string
  maxConsecutiveFailures: string
}

const EMPTY_BUDGET: BudgetDraft = {
  maxRuns: "0",
  maxTokens: "0",
  maxConsecutiveFailures: "0",
}

/** 一行自动化任务：概要、开关、操作按钮，以及点开才出现的循环状态。
 *
 * 导出是为了让测试能只渲染这一行就钉住它的动画约定（列表行错峰、状态面板展开、
 * 一次性提示），不必把整页连同会话与模型列表一起搭起来。
 */
export function AutomationRow({
  schedule,
  index,
  disabled,
  onEdit,
  onDelete,
  onToggle,
}: {
  schedule: AgentSchedule
  index: number
  disabled: boolean
  onEdit: () => void
  onDelete: () => void
  onToggle: (enabled: boolean) => void
}) {
  const runSchedule = useRunAgentSchedule()
  const resumeSchedule = useResumeAgentSchedule()
  const resetSchedule = useResetAgentSchedule()
  const updateBudget = useUpdateAgentScheduleBudget()
  const [stateOpen, setStateOpen] = useState(false)
  const [receipt, setReceipt] = useState<AutomationRunReceipt | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)
  const [budgetDraft, setBudgetDraft] = useState<BudgetDraft>(EMPTY_BUDGET)
  const [budgetError, setBudgetError] = useState<string | null>(null)
  const loopState = useAgentScheduleState(schedule.id, { enabled: stateOpen })

  const stopSource = loopState.data?.stopSource ?? null
  // 五个停止来源里，只有这两种能靠「清标记」救回来；轮数 / token / 连续未通过
  // 那三条得去调高预算上限，给按钮等于给一个点了没反应的按钮。
  const resumable = stopSource === "marked" || stopSource === "awaiting_approval"

  // 打开面板、或保存之后状态刷新时，把三个上限同步成服务端的当前值。
  const budget = loopState.data?.state.budget
  useEffect(() => {
    if (!budget) return
    setBudgetDraft({
      maxRuns: String(budget.maxRuns),
      maxTokens: String(budget.maxTokens),
      maxConsecutiveFailures: String(budget.maxConsecutiveFailures),
    })
  }, [budget])

  const saveBudget = () => {
    setBudgetError(null)
    const entries = [
      ["maxRuns", budgetDraft.maxRuns],
      ["maxTokens", budgetDraft.maxTokens],
      ["maxConsecutiveFailures", budgetDraft.maxConsecutiveFailures],
    ] as const
    const body: Record<string, number> = {}
    for (const [key, raw] of entries) {
      const text = raw.trim()
      // 空串会被 Number() 变成 0（= 不限制），那是「悄悄改了数值」，当场拦下来。
      if (!/^\d+$/.test(text)) {
        setBudgetError("三个上限都要填非负整数，写 0 表示不限制。")
        return
      }
      body[key] = Number(text)
    }
    updateBudget.mutate(
      { scheduleId: schedule.id, body },
      {
        onError: (reason) =>
          setBudgetError(
            reason instanceof Error ? reason.message : "保存失败，请稍后重试。"
          ),
      }
    )
  }

  const run = () => {
    setReceipt(null)
    setRunError(null)
    runSchedule.mutate(schedule, {
      onSuccess: (data) => setReceipt(data),
      onError: (reason) =>
        setRunError(
          reason instanceof Error ? reason.message : "触发失败，请稍后重试。"
        ),
    })
  }

  return (
    <div
      className={cn(
        "automation-row flex flex-col gap-3 px-4 py-3 transition-[background-color,opacity] duration-150",
        !schedule.enabled && "opacity-60"
      )}
      style={{ animationDelay: `${Math.min(index * 35, 140)}ms` }}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 gap-3">
          <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <RobotIcon className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 className="truncate text-xs font-medium">{schedule.name}</h3>
              {schedule.lastError && (
                <span
                  className="text-[0.625rem] text-destructive"
                  title={schedule.lastError}
                >
                  最近运行失败
                </span>
              )}
            </div>
            <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
              {schedule.prompt}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.625rem] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <ClockIcon />
                {describeCron(schedule.schedule)}
              </span>
              {schedule.lastTriggeredAt && (
                <span title={schedule.lastTriggeredAt}>
                  {describeLastTriggered(schedule.lastTriggeredAt)}
                </span>
              )}
              {schedule.repo && <span className="truncate">{schedule.repo}</span>}
              <span className="font-mono">{schedule.model}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center justify-end gap-1 sm:shrink-0">
          <Switch
            checked={schedule.enabled}
            disabled={disabled}
            aria-label={schedule.enabled ? "停用任务" : "启用任务"}
            onCheckedChange={onToggle}
          />
          <Button
            variant="outline"
            size="sm"
            className="mr-1"
            disabled={runSchedule.isPending}
            title="不等计划时间，现在就跑一轮"
            onClick={run}
          >
            <PlayIcon />
            {runSchedule.isPending ? "已提交" : "立即跑一轮"}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="循环状态"
            title="循环状态"
            aria-expanded={stateOpen}
            onClick={() => setStateOpen((open) => !open)}
          >
            <CaretDownIcon
              className={cn(
                "transition-transform duration-150",
                !stateOpen && "-rotate-90"
              )}
            />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="编辑任务"
            title="编辑任务"
            onClick={onEdit}
          >
            <PencilSimpleIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="删除任务"
            title="删除任务"
            onClick={onDelete}
          >
            <TrashIcon />
          </Button>
        </div>
      </div>

      {receipt && (
        <p
          className="surface-inline-note flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground"
        >
          <span>
            已开始第 {receipt.roundNumber} 轮，并已出现在会话列表里。
          </span>
          <Link
            to="/agents/$threadId"
            params={{ threadId: receipt.threadId }}
            className="font-medium text-foreground underline underline-offset-2"
          >
            查看这一轮
          </Link>
        </p>
      )}
      {runError && (
        <p className="surface-inline-note text-xs text-destructive">
          {runError}
        </p>
      )}

      {stateOpen && (
        <div className="surface-panel-expand rounded-md border border-border bg-muted/40 p-3">
          {loopState.isLoading ? (
            <p className="text-xs text-muted-foreground">正在读取循环状态…</p>
          ) : loopState.isError ? (
            <p className="text-xs text-destructive">无法读取循环状态。</p>
          ) : (
            <>
              {loopState.data?.stopped && (
                <div className="mb-2 space-y-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <p className="text-xs text-destructive">
                      这个循环已经停了：
                      {loopState.data.stopReason ?? "原因未知"}
                    </p>
                    {resumable ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={resumeSchedule.isPending}
                        title={
                          stopSource === "awaiting_approval"
                            ? "清掉「上一轮在等人工确认」这个标记，让这个循环重新可以触发"
                            : "清掉停止标记，让这个循环重新可以触发"
                        }
                        onClick={() => {
                          setResumeError(null)
                          resumeSchedule.mutate(schedule.id, {
                            onError: (reason) =>
                              setResumeError(
                                reason instanceof Error
                                  ? reason.message
                                  : "恢复失败，请稍后重试。"
                              ),
                          })
                        }}
                      >
                        {resumeSchedule.isPending ? "正在恢复" : "继续运行"}
                      </Button>
                    ) : (
                      <span className="text-[0.625rem] text-muted-foreground">
                        这条不是手动停的，清标记没用 ——
                        要调高对应的预算上限才会重新跑起来。
                      </span>
                    )}
                  </div>
                  {stopSource === "awaiting_approval" && (
                    <p className="text-[0.625rem] text-muted-foreground">
                      先到那一轮会话里把待审批的命令处理掉，再点「继续运行」——
                      没人处理的话，下一轮还会卡在同一条命令上。
                    </p>
                  )}
                  {resumeError && (
                    <p
                      className="surface-inline-note text-[0.625rem] text-destructive"
                    >
                      {resumeError}
                    </p>
                  )}
                </div>
              )}
              {loopState.data?.text ? (
                <Markdown content={loopState.data.text} />
              ) : (
                <p className="text-xs text-muted-foreground">还没有运行记录。</p>
              )}
              {loopState.data && (
                <div className="mt-3 border-t border-border pt-3">
                  <p className="text-[0.625rem] font-medium">预算上限</p>
                  <p className="mt-0.5 text-[0.625rem] text-muted-foreground">
                    写 0 表示不限制。调高上限是「因预算用完而停下」的循环唯一的恢复办法。
                  </p>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <Field label="最多跑几轮" htmlFor={`budget-runs-${schedule.id}`}>
                      <Input
                        id={`budget-runs-${schedule.id}`}
                        inputMode="numeric"
                        value={budgetDraft.maxRuns}
                        onChange={(event) =>
                          setBudgetDraft((draft) => ({
                            ...draft,
                            maxRuns: event.target.value,
                          }))
                        }
                      />
                    </Field>
                    <Field
                      label="最多消耗 token"
                      htmlFor={`budget-tokens-${schedule.id}`}
                    >
                      <Input
                        id={`budget-tokens-${schedule.id}`}
                        inputMode="numeric"
                        value={budgetDraft.maxTokens}
                        onChange={(event) =>
                          setBudgetDraft((draft) => ({
                            ...draft,
                            maxTokens: event.target.value,
                          }))
                        }
                      />
                    </Field>
                    <Field
                      label="连续未通过几次就停"
                      htmlFor={`budget-failures-${schedule.id}`}
                    >
                      <Input
                        id={`budget-failures-${schedule.id}`}
                        inputMode="numeric"
                        value={budgetDraft.maxConsecutiveFailures}
                        onChange={(event) =>
                          setBudgetDraft((draft) => ({
                            ...draft,
                            maxConsecutiveFailures: event.target.value,
                          }))
                        }
                      />
                    </Field>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={updateBudget.isPending}
                      onClick={saveBudget}
                    >
                      {updateBudget.isPending ? "正在保存" : "保存上限"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={resetSchedule.isPending}
                      title="把轮数、token 用量、连续未通过次数与停止标记一起归零，任务本身保留"
                      onClick={() => {
                        setResetError(null)
                        setResetOpen(true)
                      }}
                    >
                      清空历史
                    </Button>
                    {budgetError && (
                      <span
                        className="surface-inline-note text-[0.625rem] text-destructive"
                      >
                        {budgetError}
                      </span>
                    )}
                    {resetError && (
                      <span
                        className="surface-inline-note text-[0.625rem] text-destructive"
                      >
                        {resetError}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={resetOpen}
        title="清空循环历史"
        description={`将把“${schedule.name}”的轮数、token 用量、连续未通过次数与停止标记一起归零，已有会话记录不受影响。任务配置保留。`}
        confirmText="清空"
        destructive
        busy={resetSchedule.isPending}
        onOpenChange={setResetOpen}
        onConfirm={() => {
          resetSchedule.mutate(schedule.id, {
            onSuccess: () => setResetOpen(false),
            onError: (error: unknown) => {
              // 关掉对话框再看错误：对话框里没有位置显示它，留着只会让人以为没反应。
              setResetOpen(false)
              setResetError(
                error instanceof Error ? error.message : "清空历史失败"
              )
            },
          })
        }}
      />
    </div>
  )
}

function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string
  hint?: string
  htmlFor: string
  children: ReactNode
}) {
  return (
    <div className="space-y-1">
      <label
        htmlFor={htmlFor}
        className="flex items-center gap-1 text-xs font-medium"
      >
        {label}
        {hint && (
          <span className="font-normal text-muted-foreground">{hint}</span>
        )}
      </label>
      {children}
    </div>
  )
}

/** 空态与错误态共用的一屏提示。
 *
 * 导出是为了让测试能直接钉住它挂的动画类 —— 与技能接口页
 * （``routes/skills.tsx``）的空态用的是同一个类，两处必须一致。
 */
export function StateMessage({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="surface-state-enter flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">
      {icon && <div className="mb-3 text-muted-foreground">{icon}</div>}
      <p className="text-xs font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
        {description}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

function PageSkeleton() {
  return (
    <main className="p-6">
      <Skeleton className="h-40 w-full" />
    </main>
  )
}
