import { Navigate, createFileRoute } from "@tanstack/react-router"
import {
  CalendarBlankIcon,
  ClockIcon,
  PencilSimpleIcon,
  PlusIcon,
  RobotIcon,
  TrashIcon,
} from "@phosphor-icons/react"
import { useEffect, useMemo, useState, type ReactNode } from "react"

import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { CRON_PRESETS, describeCron, presetForCron } from "@/lib/agents/cron"
import {
  useAgentSchedules,
  useCreateAgentSchedule,
  useDeleteAgentSchedule,
  useUpdateAgentSchedule,
} from "@/lib/agents/queries"
import type { AgentSchedule } from "@/lib/agents/types"
import { useModelOptions } from "@/lib/agents/useModelOptions"
import { useSession } from "@/lib/session"
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
  modelId: string
  effort: string
}

const EMPTY_FORM: FormState = {
  name: "",
  prompt: "",
  schedule: "0 9 * * *",
  preset: "daily",
  repo: "",
  modelId: "",
  effort: "default",
}

function AutomationsPage() {
  const session = useSession()
  if (session.isLoading) return <PageSkeleton />
  if (!session.data) return <Navigate to="/login" />
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
        effort: modelOptions.defaultSelection?.effort ?? "default",
      }))
    }
  }, [form.modelId, modelOptions.defaultSelection])

  const selectedModel = useMemo(
    () => modelOptions.models.find((model) => model.id === form.modelId),
    [form.modelId, modelOptions.models]
  )

  const resetForm = () => {
    setEditing(null)
    setForm({
      ...EMPTY_FORM,
      modelId: modelOptions.defaultSelection?.modelId ?? "",
      effort: modelOptions.defaultSelection?.effort ?? "default",
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
      modelId: schedule.model,
      effort: schedule.effort ?? "default",
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
      effort: form.effort || null,
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
            {items.map((item) => (
              <AutomationRow
                key={item.id}
                schedule={item}
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
                <Input
                  id="automation-repo"
                  value={form.repo}
                  placeholder="owner/repository"
                  onChange={(event) =>
                    setForm({ ...form, repo: event.target.value })
                  }
                />
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
                <select
                  id="automation-preset"
                  className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
                  value={form.preset}
                  onChange={(event) => {
                    const preset = event.target.value as FormState["preset"]
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
                  {CRON_PRESETS.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.label}
                    </option>
                  ))}
                  <option value="custom">自定义</option>
                </select>
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
                <select
                  id="automation-model"
                  className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
                  value={form.modelId}
                  onChange={(event) => {
                    const model = modelOptions.models.find(
                      (item) => item.id === event.target.value
                    )
                    setForm({
                      ...form,
                      modelId: event.target.value,
                      effort: model?.default_effort ?? "default",
                    })
                  }}
                >
                  {modelOptions.models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {selectedModel && selectedModel.efforts.length > 1 && (
              <Field label="推理强度" htmlFor="automation-effort">
                <select
                  id="automation-effort"
                  className="h-7 rounded-md border border-input bg-background px-2 text-xs"
                  value={form.effort}
                  onChange={(event) =>
                    setForm({ ...form, effort: event.target.value })
                  }
                >
                  {selectedModel.efforts.map((effort) => (
                    <option key={effort} value={effort}>
                      {effort}
                    </option>
                  ))}
                </select>
              </Field>
            )}
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

function AutomationRow({
  schedule,
  disabled,
  onEdit,
  onDelete,
  onToggle,
}: {
  schedule: AgentSchedule
  disabled: boolean
  onEdit: () => void
  onDelete: () => void
  onToggle: (enabled: boolean) => void
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center",
        !schedule.enabled && "opacity-60"
      )}
    >
      <div className="flex min-w-0 flex-1 gap-3">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <RobotIcon className="size-4" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="truncate text-xs font-medium">{schedule.name}</h3>
            {schedule.lastError && (
              <span className="text-[0.625rem] text-destructive">
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

function StateMessage({
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
    <div className="flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">
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
