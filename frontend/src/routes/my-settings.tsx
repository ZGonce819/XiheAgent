import { Navigate, createFileRoute, useLocation } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Fragment, useEffect, useState, type Dispatch, type SetStateAction } from "react"
import {
  PencilIcon,
  TrashIcon,
  PlusIcon,
  CheckIcon,
} from "@phosphor-icons/react"

import type { CustomModel, CustomModelInput, BuiltinModel } from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/my-settings")({
  component: MySettingsPage,
})

const EMPTY_FORM: FormState = {
  name: "",
  api_base: "",
  api_key: "",
  model_name: "",
  max_tokens: 4096,
  temperature: 0.7,
  is_default: false,
}

interface FormState extends CustomModelInput {
  is_default: boolean
}

function AnimatedCheckbox({
  checked,
  label,
  onChange,
}: {
  checked: boolean
  label: string
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="group flex cursor-pointer items-center gap-1.5 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="peer sr-only"
      />
      <span
        className={cn(
          "grid size-4 shrink-0 place-items-center rounded border transition-[border-color,background-color,transform,box-shadow] duration-150",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-ring/30",
          checked
            ? "scale-100 border-primary bg-primary text-primary-foreground"
            : "border-input bg-background text-transparent group-hover:border-foreground/40"
        )}
      >
        <CheckIcon
          className={cn(
            "size-3 transition-[transform,opacity] duration-150 ease-out motion-reduce:transition-opacity",
            checked
              ? "scale-100 rotate-0 opacity-100"
              : "scale-50 -rotate-12 opacity-0"
          )}
          weight="bold"
        />
      </span>
      {label}
    </label>
  )
}

function ModelForm({
  form,
  setForm,
  isEditing,
  isEditingBuiltin,
  saving,
  onSave,
  onCancel,
}: {
  form: FormState
  setForm: Dispatch<SetStateAction<FormState>>
  isEditing: boolean
  isEditingBuiltin: boolean
  saving: boolean
  onSave: () => void
  onCancel: () => void
}) {
  return (
    <div className="animate-in space-y-3 p-4 duration-200 fade-in-0 slide-in-from-top-2 motion-reduce:fade-in-0 motion-reduce:slide-in-from-top-0">
      {isEditingBuiltin && (
        <p className="text-xs text-muted-foreground">
          内置模型的名称和模型 ID 由环境配置决定，不可修改。
        </p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium">显示名称</label>
          <Input
            value={form.name}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                name: event.target.value,
              }))
            }
            placeholder="如：DeepSeek V3"
            disabled={isEditingBuiltin}
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium">模型 ID</label>
          <Input
            value={form.model_name}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                model_name: event.target.value,
              }))
            }
            placeholder="如：deepseek-chat"
            disabled={isEditingBuiltin}
          />
        </div>
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium">API Base URL</label>
        <Input
          value={form.api_base}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              api_base: event.target.value,
            }))
          }
          placeholder="如：https://api.deepseek.com"
          disabled={isEditingBuiltin}
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs font-medium">API Key</label>
        <Input
          type="password"
          value={form.api_key ?? ""}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              api_key: event.target.value,
            }))
          }
          placeholder={
            isEditing
              ? isEditingBuiltin
                ? "留空使用 .env 配置"
                : "留空保持不变"
              : "sk-..."
          }
        />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1">
          <label className="text-xs font-medium">Max Tokens</label>
          <Input
            type="number"
            value={form.max_tokens}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                max_tokens: Number(event.target.value),
              }))
            }
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-medium">Temperature</label>
          <Input
            type="number"
            step="0.1"
            min="0"
            max="2"
            value={form.temperature}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                temperature: Number(event.target.value),
              }))
            }
          />
        </div>
        <div className="flex items-end gap-3 pb-1">
          <AnimatedCheckbox
            checked={form.is_default}
            label="默认"
            onChange={(checked) =>
              setForm((current) => ({ ...current, is_default: checked }))
            }
          />
        </div>
      </div>
      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" onClick={onSave} disabled={saving}>
          {isEditing ? "保存修改" : "添加模型"}
        </Button>
        <Button size="sm" variant="outline" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  )
}

function CustomModelsSection() {
  const location = useLocation()
  const qc = useQueryClient()
  const models = useQuery({
    queryKey: ["customModels"],
    queryFn: api.listCustomModels,
  })
  const builtin = useQuery({
    queryKey: ["builtinModel"],
    queryFn: api.getBuiltinModel,
  })
  const [editing, setEditing] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>({ ...EMPTY_FORM })
  const [error, setError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<CustomModel | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [defaultFeedbackId, setDefaultFeedbackId] = useState<string | null>(
    null
  )

  const startAdd = () => {
    setError(null)
    setShowForm(true)
    setEditing(null)
    setForm({ ...EMPTY_FORM })
  }

  useEffect(() => {
    if (location.state?.addModel) {
      startAdd()
    }
  }, [location.state])

  const isEditingBuiltin = editing === "__builtin__"

  const createMut = useMutation({
    mutationFn: (body: CustomModelInput) => api.createCustomModel(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["customModels"] })
      void qc.invalidateQueries({ queryKey: ["builtinModel"] })
      void qc.invalidateQueries({ queryKey: ["options"] })
      setShowForm(false)
      setForm({ ...EMPTY_FORM })
    },
    onError: (e: Error) => setError(e.message),
  })

  const updateMut = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string
      body: Partial<CustomModelInput>
    }) => api.updateCustomModel(id, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["customModels"] })
      void qc.invalidateQueries({ queryKey: ["builtinModel"] })
      void qc.invalidateQueries({ queryKey: ["options"] })
      setEditing(null)
    },
    onError: (e: Error) => setError(e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteCustomModel(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["customModels"] })
      const previous = qc.getQueryData<Array<CustomModel>>(["customModels"])
      qc.setQueryData<Array<CustomModel>>(["customModels"], (current) =>
        (current ?? []).filter((model) => model.id !== id)
      )
      return { previous }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["customModels"] })
      void qc.invalidateQueries({ queryKey: ["builtinModel"] })
      void qc.invalidateQueries({ queryKey: ["options"] })
    },
    onError: (e: Error, _id, context) => {
      if (context?.previous) {
        qc.setQueryData(["customModels"], context.previous)
      }
      setError(e.message)
    },
    onSettled: () => setDeletingId(null),
  })

  const setDefaultMut = useMutation({
    mutationFn: (target: { id: string; kind: "builtin" | "custom" }) =>
      target.kind === "builtin"
        ? api.updateBuiltinModel({ is_default: true })
        : api.updateCustomModel(target.id, { set_default: true }),
    onMutate: async (target) => {
      await Promise.all([
        qc.cancelQueries({ queryKey: ["builtinModel"] }),
        qc.cancelQueries({ queryKey: ["customModels"] }),
      ])
      const previousBuiltin = qc.getQueryData<BuiltinModel>(["builtinModel"])
      const previousCustom = qc.getQueryData<Array<CustomModel>>([
        "customModels",
      ])
      qc.setQueryData<BuiltinModel>(["builtinModel"], (current) =>
        current
          ? { ...current, is_default: target.kind === "builtin" }
          : current
      )
      qc.setQueryData<Array<CustomModel>>(["customModels"], (current) =>
        (current ?? []).map((model) => ({
          ...model,
          is_default: target.kind === "custom" && model.id === target.id,
        }))
      )
      setDefaultFeedbackId(target.id)
      return { previousBuiltin, previousCustom }
    },
    onError: (e: Error, _target, context) => {
      if (context?.previousBuiltin) {
        qc.setQueryData(["builtinModel"], context.previousBuiltin)
      }
      if (context?.previousCustom) {
        qc.setQueryData(["customModels"], context.previousCustom)
      }
      setDefaultFeedbackId(null)
      setError(e.message)
    },
    onSuccess: () => {
      window.setTimeout(() => setDefaultFeedbackId(null), 450)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["builtinModel"] })
      void qc.invalidateQueries({ queryKey: ["customModels"] })
      void qc.invalidateQueries({ queryKey: ["options"] })
    },
  })

  const builtinMut = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.updateBuiltinModel(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["builtinModel"] })
      void qc.invalidateQueries({ queryKey: ["customModels"] })
      void qc.invalidateQueries({ queryKey: ["options"] })
      setEditing(null)
    },
    onError: (e: Error) => setError(e.message),
  })

  const startEditBuiltin = () => {
    if (editing === "__builtin__") {
      cancelEdit()
      return
    }
    const b = builtin.data
    if (!b) return
    setError(null)
    setShowForm(false)
    setEditing("__builtin__")
    setForm({
      name: b.name,
      api_base: b.api_base,
      api_key: "",
      model_name: b.model_name,
      max_tokens: b.max_tokens,
      temperature: b.temperature,
      is_default: b.is_default,
    })
  }

  const startEdit = (m: CustomModel) => {
    if (editing === m.id) {
      cancelEdit()
      return
    }
    setError(null)
    setEditing(m.id)
    setShowForm(false)
    setForm({
      name: m.name,
      api_base: m.api_base,
      api_key: "",
      model_name: m.model_name,
      max_tokens: m.max_tokens,
      temperature: m.temperature,
      is_default: m.is_default,
    })
  }

  const cancelEdit = () => {
    setEditing(null)
    setShowForm(false)
    setForm({ ...EMPTY_FORM })
  }

  const handleSave = () => {
    if (!isEditingBuiltin && !form.name) {
      setError("名称不能为空")
      return
    }
    if (!isEditingBuiltin && !form.api_base) {
      setError("API 地址不能为空")
      return
    }
    if (!isEditingBuiltin && !form.model_name) {
      setError("模型名不能为空")
      return
    }
    if (isEditingBuiltin) {
      builtinMut.mutate({
        api_key: form.api_key || undefined,
        max_tokens: form.max_tokens,
        temperature: form.temperature,
        is_default: form.is_default,
      })
    } else if (editing) {
      updateMut.mutate({
        id: editing,
        body: { ...form, set_default: form.is_default },
      })
    } else {
      const { is_default, ...model } = form
      createMut.mutate({ ...model, set_default: is_default })
    }
  }

  const b = builtin.data

  const makeDefault = (target: { id: string; kind: "builtin" | "custom" }) => {
    if (editing) {
      setForm((current) => ({
        ...current,
        is_default: editing === target.id,
      }))
    }
    setDefaultMut.mutate(target)
  }

  const confirmDelete = () => {
    if (!deleteTarget || deletingId) return
    const id = deleteTarget.id
    setDeleteTarget(null)
    if (editing === id) cancelEdit()
    setDeletingId(id)
    window.setTimeout(() => deleteMut.mutate(id), 180)
  }

  const renderEditForm = (key: string) => (
    <ModelForm
      key={key}
      form={form}
      setForm={setForm}
      isEditing
      isEditingBuiltin={isEditingBuiltin}
      saving={updateMut.isPending || builtinMut.isPending}
      onSave={handleSave}
      onCancel={cancelEdit}
    />
  )

  return (
    <SettingsSection
      title="自定义模型"
      description="管理内置模型和自定义模型，设为默认后 Agent 将使用该模型进行推理。"
      contentClassName="overflow-hidden sm:rounded-lg"
    >
      <div className="divide-y divide-border">
        {/* ── 内置模型行 ── */}
        {b && (
          <Fragment>
            <div
              className={cn(
                "flex animate-in items-center justify-between gap-4 px-4 py-3 duration-200 fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none",
                defaultFeedbackId === "__builtin__" && "model-default-flash"
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{b.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
                    {b.model_name}
                  </span>
                  <span className="rounded bg-primary/5 px-1.5 py-0.5 text-[0.625rem] text-primary/70">
                    内置
                  </span>
                  {b.is_default && (
                    <span className="model-default-badge rounded-full bg-primary/10 px-2 py-0.5 text-[0.625rem] font-medium text-primary">
                      默认
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {b.api_base} · Key: {b.api_key_masked} · {b.max_tokens} tokens
                  · T={b.temperature}
                </div>
              </div>
              <div className="flex items-center gap-1">
                {!b.is_default && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs"
                    disabled={setDefaultMut.isPending}
                    onClick={() =>
                      makeDefault({
                        id: "__builtin__",
                        kind: "builtin",
                      })
                    }
                  >
                    <CheckIcon className="mr-1 size-3.5" />
                    设为默认
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0"
                  onClick={startEditBuiltin}
                  aria-label={
                    isEditingBuiltin ? "关闭内置模型编辑" : "编辑内置模型"
                  }
                  title={isEditingBuiltin ? "关闭编辑" : "编辑模型"}
                >
                  <PencilIcon className="size-3.5" />
                </Button>
              </div>
            </div>
            {isEditingBuiltin && renderEditForm("__builtin__")}
          </Fragment>
        )}
        {builtin.isLoading && (
          <div className="px-4 py-3">
            <Skeleton className="h-10 w-full" />
          </div>
        )}

        {/* ── 自定义模型行 ── */}
        {models.data?.map((m, index) => (
          <Fragment key={m.id}>
            <div
              className={cn(
                "flex animate-in items-center justify-between gap-4 px-4 py-3 duration-200 fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none",
                defaultFeedbackId === m.id && "model-default-flash",
                deletingId === m.id && "model-row-exit pointer-events-none"
              )}
              style={{
                animationDelay: `${Math.min(index * 35, 140)}ms`,
                animationFillMode: "both",
              }}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{m.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
                    {m.model_name}
                  </span>
                  {m.is_default && (
                    <span className="model-default-badge rounded-full bg-primary/10 px-2 py-0.5 text-[0.625rem] font-medium text-primary">
                      默认
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {m.api_base} · Key: {m.api_key} · {m.max_tokens} tokens · T=
                  {m.temperature}
                </div>
              </div>
              <div className="flex items-center gap-1">
                {!m.is_default && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs"
                    disabled={setDefaultMut.isPending}
                    onClick={() => makeDefault({ id: m.id, kind: "custom" })}
                  >
                    <CheckIcon className="mr-1 size-3.5" />
                    设为默认
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0"
                  onClick={() => startEdit(m)}
                  aria-label={
                    editing === m.id ? `关闭 ${m.name} 编辑` : `编辑 ${m.name}`
                  }
                  title={editing === m.id ? "关闭编辑" : "编辑模型"}
                >
                  <PencilIcon className="size-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                  onClick={() => setDeleteTarget(m)}
                  disabled={deletingId !== null}
                  aria-label={`删除 ${m.name}`}
                  title="删除模型"
                >
                  <TrashIcon className="size-3.5" />
                </Button>
              </div>
            </div>
            {editing === m.id && renderEditForm(m.id)}
          </Fragment>
        ))}

        {models.data?.length === 0 && !showForm && !editing && (
          <div className="flex min-h-36 animate-in flex-col items-center justify-center px-6 py-8 text-center duration-200 fade-in-0 zoom-in-95 motion-reduce:animate-none">
            <div className="mb-3 flex size-9 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <PlusIcon className="size-4" />
            </div>
            <p className="text-sm font-medium text-foreground">
              还没有自定义模型
            </p>
            <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
              添加一个 OpenAI 兼容模型，保存后即可在 Agent 中选择使用。
            </p>
            <Button size="sm" className="mt-4" onClick={startAdd}>
              <PlusIcon className="size-3.5" />
              添加模型
            </Button>
          </div>
        )}
      </div>

      {/* ── 编辑/新增表单 ── */}
      {showForm && (
        <div
          key={editing ?? "__new__"}
          className="animate-in space-y-3 border-t border-border p-4 duration-200 fade-in-0 slide-in-from-top-2 motion-reduce:fade-in-0 motion-reduce:slide-in-from-top-0"
        >
          {isEditingBuiltin && (
            <p className="text-xs text-muted-foreground">
              内置模型的名称和模型 ID 由环境配置决定，不可修改。
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium">显示名称</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="如：DeepSeek V3"
                disabled={isEditingBuiltin}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">模型 ID</label>
              <Input
                value={form.model_name}
                onChange={(e) =>
                  setForm({ ...form, model_name: e.target.value })
                }
                placeholder="如：deepseek-chat"
                disabled={isEditingBuiltin}
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">API Base URL</label>
            <Input
              value={form.api_base}
              onChange={(e) => setForm({ ...form, api_base: e.target.value })}
              placeholder="如：https://api.deepseek.com"
              disabled={isEditingBuiltin}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium">API Key</label>
            <Input
              type="password"
              value={form.api_key ?? ""}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder={
                editing
                  ? isEditingBuiltin
                    ? "留空使用 .env 配置"
                    : "留空保持不变"
                  : "sk-..."
              }
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <label className="text-xs font-medium">Max Tokens</label>
              <Input
                type="number"
                value={form.max_tokens}
                onChange={(e) =>
                  setForm({ ...form, max_tokens: Number(e.target.value) })
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium">Temperature</label>
              <Input
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={form.temperature}
                onChange={(e) =>
                  setForm({ ...form, temperature: Number(e.target.value) })
                }
              />
            </div>
            <div className="flex items-end gap-3 pb-1">
              <AnimatedCheckbox
                checked={form.is_default}
                label="默认"
                onChange={(checked) =>
                  setForm({ ...form, is_default: checked })
                }
              />
            </div>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              onClick={handleSave}
              disabled={
                createMut.isPending ||
                updateMut.isPending ||
                builtinMut.isPending
              }
            >
              {editing ? "保存修改" : "添加模型"}
            </Button>
            <Button size="sm" variant="outline" onClick={cancelEdit}>
              取消
            </Button>
          </div>
        </div>
      )}

      {!showForm && !editing && (models.data?.length ?? 0) > 0 && (
        <div className="border-t border-border px-4 py-3">
          <Button
            size="sm"
            variant="outline"
            className="animate-in duration-150 fade-in-0 motion-reduce:animate-none"
            onClick={startAdd}
          >
            <PlusIcon className="mr-1 size-4" />
            添加模型
          </Button>
        </div>
      )}

      {error && (
        <p className="border-t border-border px-4 py-3 text-xs text-destructive">
          {error}
        </p>
      )}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除自定义模型"
        description={
          deleteTarget
            ? `将永久删除“${deleteTarget.name}”的模型配置，此操作无法撤销。`
            : ""
        }
        confirmText="删除"
        destructive
        busy={deleteMut.isPending}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        onConfirm={confirmDelete}
      />
    </SettingsSection>
  )
}

function MySettingsPage() {
  const session = useSession()

  if (session.isLoading) {
    return (
      <main className="p-6">
        <Skeleton className="h-40 w-full" />
      </main>
    )
  }
  if (!session.data) return <Navigate to="/login" />

  return (
    <AppShell user={session.data} title="个人设置">
      <CustomModelsSection />
    </AppShell>
  )
}
