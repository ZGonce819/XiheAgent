import { createFileRoute, useLocation } from "@tanstack/react-router"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Fragment,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react"
import {
  PencilIcon,
  TrashIcon,
  PlusIcon,
  CheckIcon,
  CheckCircleIcon,
  FolderIcon,
  FolderOpenIcon,
} from "@phosphor-icons/react"

import type {
  CustomModel,
  CustomModelInput,
  BuiltinModel,
  CommandApprovalSettings,
  SkillRetrievalSettings,
} from "@/lib/api"
import { AppShell, SettingsSection } from "@/components/AppShell"
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
import { api } from "@/lib/api"
import { useSession } from "@/lib/session"
import { cn, validatePath } from "@/lib/utils"

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
    const locationState = location.state as typeof location.state & {
      addModel?: boolean
    }
    if (locationState?.addModel) {
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

function CommandApprovalSection() {
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: ["commandApprovalSettings"],
    queryFn: api.getCommandApprovalSettings,
  })
  const [draft, setDraft] = useState<CommandApprovalSettings | null>(null)
  const [savingEnv, setSavingEnv] = useState<"local" | "sandbox" | null>(
    null,
  )
  const [lastSavedEnv, setLastSavedEnv] = useState<
    "local" | "sandbox" | null
  >(null)
  const save = useMutation({
    mutationFn: ({ value }: {
      value: CommandApprovalSettings
      environment: "local" | "sandbox"
    }) =>
      api.saveCommandApprovalSettings({
        local: value.local,
        sandbox: value.sandbox,
      }),
    onSuccess: (value, variables) => {
      setDraft(value)
      setSavingEnv(null)
      setLastSavedEnv(variables.environment)
      void queryClient.invalidateQueries({
        queryKey: ["commandApprovalSettings"],
      })
    },
    onError: () => {
      setSavingEnv(null)
    },
  })
  const value = draft ?? settings.data
  if (!value) return null
  const setAction = (
    environment: "local" | "sandbox",
    id: string,
    action: "allow" | "approval"
  ) => {
    setDraft({
      ...value,
      [environment]: { ...value[environment], [id]: action },
    })
    setLastSavedEnv(null)
  }
  const handleSave = (environment: "local" | "sandbox") => {
    setLastSavedEnv(environment)
    setSavingEnv(environment)
    save.mutate({ value, environment })
  }
  return (
    <SettingsSection
      title="命令审批"
      description="为本地工作区和沙箱分别设置普通操作的默认行为。高危规则由系统锁定，无法在此放开。"
      contentClassName="overflow-hidden rounded-lg border"
    >
      <div className="divide-y divide-border">
        {(["local", "sandbox"] as const).map((environment) => (
          <div key={environment} className="p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">
                  {environment === "local" ? "本地工作区" : "OpenSandbox 沙箱"}
                </p>
                <p className="text-xs text-muted-foreground">
                  {environment === "local"
                    ? "更保守，适合访问真实电脑"
                    : "隔离环境内可自动执行更多可恢复操作"}
                </p>
              </div>
              <Button
                key={environment}
                size="sm"
                onClick={() => handleSave(environment)}
                disabled={save.isPending}
              >
                {save.isSuccess && !save.isPending && lastSavedEnv === environment ? (
                  <CheckIcon
                    className="approval-save-check size-3.5"
                    weight="bold"
                  />
                ) : null}
                {savingEnv === environment
                  ? "保存中"
                  : save.isSuccess && lastSavedEnv === environment
                    ? "已保存"
                    : "保存设置"}
              </Button>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {value.categories.map((category, index) => {
                const currentAction =
                  value[environment][category.id] ?? "approval"
                const actionLabel =
                  currentAction === "allow" ? "自动允许" : "每次询问"
                return (
                  <label
                    key={category.id}
                    className="approval-policy-row flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5 text-xs"
                    style={{ animationDelay: `${Math.min(index * 24, 120)}ms` }}
                  >
                    <span className="min-w-0">
                      <span className="block font-medium">
                        {category.label}
                      </span>
                      <span className="mt-0.5 block text-muted-foreground">
                        {category.description}
                      </span>
                    </span>
                    <Select
                      value={currentAction}
                      onValueChange={(next) =>
                        setAction(
                          environment,
                          category.id,
                          next as "allow" | "approval"
                        )
                      }
                    >
                      <SelectTrigger
                        size="sm"
                        className="approval-policy-select w-[6.5rem] shrink-0 bg-background"
                        aria-label={`${category.label}审批策略`}
                      >
                        <span className="flex flex-1 text-left">
                          {actionLabel}
                        </span>
                      </SelectTrigger>
                      <SelectContent
                        align="end"
                        alignItemWithTrigger={false}
                        className="approval-policy-menu min-w-[8rem] origin-top-right animate-in duration-150 ease-out fade-in-0 zoom-in-95 slide-in-from-top-1"
                      >
                        <SelectItem value="approval">每次询问</SelectItem>
                        <SelectItem value="allow">自动允许</SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                )
              })}
            </div>
          </div>
        ))}
        <div className="bg-muted/30 p-4">
          <p className="text-xs font-medium">始终由系统拦截或强制审批</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {value.lockedRules
              .map(
                (rule) =>
                  `${rule.label}（${rule.action === "deny" ? "直接拒绝" : "必须审批"}）`
              )
              .join(" · ")}
          </p>
        </div>
      </div>
    </SettingsSection>
  )
}

function SkillRetrievalSection() {
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: ["skillRetrievalSettings"],
    queryFn: api.getSkillRetrievalSettings,
  })
  const [draft, setDraft] = useState<SkillRetrievalSettings | null>(null)
  const save = useMutation({
    mutationFn: (value: SkillRetrievalSettings) =>
      api.saveSkillRetrievalSettings({
        mode: value.mode,
        top_k: value.top_k,
        auto_threshold: value.auto_threshold,
      }),
    onSuccess: (value) => {
      setDraft(value)
      void queryClient.invalidateQueries({
        queryKey: ["skillRetrievalSettings"],
      })
    },
  })
  const value = draft ?? settings.data
  if (!value) return null
  const update = (patch: Partial<SkillRetrievalSettings>) =>
    setDraft({ ...value, ...patch })
  const setNumber = (key: "top_k" | "auto_threshold", raw: string) => {
    if (raw.trim() === "") return
    const next = Number(raw)
    if (Number.isFinite(next)) update({ [key]: next })
  }
  const current = value.modes.find((mode) => mode.id === value.mode)
  return (
    <SettingsSection
      title="技能检索"
      description="技能列表会写进每次模型调用的系统提示词。技能变多以后可以开启检索，只列出当前任务用得上的技能。改动在下一条消息生效。"
      contentClassName="overflow-hidden rounded-lg border"
    >
      <div className="p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">检索模式</p>
            <p className="text-xs text-muted-foreground">
              {current?.description}
            </p>
          </div>
          <Button
            size="sm"
            onClick={() => save.mutate(value)}
            disabled={save.isPending}
          >
            {save.isSuccess && !save.isPending ? (
              <CheckIcon className="approval-save-check size-3.5" weight="bold" />
            ) : null}
            {save.isPending
              ? "保存中"
              : save.isSuccess
                ? "已保存"
                : "保存设置"}
          </Button>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          {value.modes.map((mode) => (
            <button
              key={mode.id}
              type="button"
              onClick={() => update({ mode: mode.id })}
              aria-pressed={value.mode === mode.id}
              className={cn(
                "rounded-md border px-3 py-2.5 text-left text-xs transition-colors",
                value.mode === mode.id
                  ? "border-foreground/40 bg-muted"
                  : "border-border hover:bg-muted/50"
              )}
            >
              <span className="block font-medium">{mode.label}</span>
              <span className="mt-0.5 block text-muted-foreground">
                {mode.description}
              </span>
            </button>
          ))}
        </div>
        {value.mode === "off" ? null : (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <label className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5 text-xs">
              <span className="min-w-0">
                <span className="block font-medium">最多列出几个技能</span>
                <span className="mt-0.5 block text-muted-foreground">
                  几个内置技能会一直列出，其余按相关度排
                </span>
              </span>
              <Input
                type="number"
                min={value.limits.top_k.min}
                max={value.limits.top_k.max}
                value={value.top_k}
                onChange={(event) => setNumber("top_k", event.target.value)}
                className="w-[5.5rem] shrink-0 bg-background"
                aria-label="最多列出几个技能"
              />
            </label>
            {value.mode === "auto" ? (
              <label className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5 text-xs">
                <span className="min-w-0">
                  <span className="block font-medium">超过几个技能开始筛选</span>
                  <span className="mt-0.5 block text-muted-foreground">
                    技能总数超过这个数时才筛选
                  </span>
                </span>
                <Input
                  type="number"
                  min={value.limits.auto_threshold.min}
                  max={value.limits.auto_threshold.max}
                  value={value.auto_threshold}
                  onChange={(event) =>
                    setNumber("auto_threshold", event.target.value)
                  }
                  className="w-[5.5rem] shrink-0 bg-background"
                  aria-label="超过几个技能开始筛选"
                />
              </label>
            ) : null}
          </div>
        )}
        <p className="mt-3 text-xs leading-5 text-muted-foreground">
          筛选只影响提示词里列出的技能，技能本身不会失效：模型需要时仍能自己找到并加载没列出的技能。
        </p>
      </div>
    </SettingsSection>
  )
}

function WorkspaceSection() {
  const queryClient = useQueryClient()
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: api.getWorkspace,
  })
  const [editing, setEditing] = useState(false)
  const [draftPath, setDraftPath] = useState("")
  const [picking, setPicking] = useState(false)

  // 项目目录 / 技能目录编辑状态
  const [editingProjects, setEditingProjects] = useState(false)
  const [draftProjects, setDraftProjects] = useState("")
  const [pickingProjects, setPickingProjects] = useState(false)

  const [editingSkills, setEditingSkills] = useState(false)
  const [draftSkills, setDraftSkills] = useState("")
  const [pickingSkills, setPickingSkills] = useState(false)
  const [migrationProgress, setMigrationProgress] = useState<{
    migrated: number
    errors: string[]
  } | null>(null)

  const update = useMutation({
    mutationFn: (body: {
      workspace_root?: string
      projects_dir?: string
      skills_dir?: string
    }) => api.updateWorkspace(body),
    onSuccess: (data) => {
      setEditing(false)
      setEditingProjects(false)
      setEditingSkills(false)
      if (data.migration) {
        setMigrationProgress(data.migration)
      }
      void queryClient.invalidateQueries({ queryKey: ["workspace"] })
    },
  })

  // 实时路径验证（禁止 C 盘）
  const pathValidation = useMemo(
    () => (draftPath.trim() ? validatePath(draftPath, { forbiddenDrives: ["C"] }) : { valid: true, error: "", platform: "unknown" as const }),
    [draftPath]
  )
  const projectsValidation = useMemo(
    () => (draftProjects.trim() ? validatePath(draftProjects, { forbiddenDrives: ["C"] }) : { valid: true, error: "", platform: "unknown" as const }),
    [draftProjects]
  )
  const skillsValidation = useMemo(
    () => (draftSkills.trim() ? validatePath(draftSkills, { forbiddenDrives: ["C"] }) : { valid: true, error: "", platform: "unknown" as const }),
    [draftSkills]
  )

  const handlePickFolder = async (
    setDraft: (v: string) => void,
    setPickingState: (v: boolean) => void,
  ) => {
    setPickingState(true)
    try {
      const result = await api.pickFolder()
      if (result.path) {
        setDraft(result.path)
      }
    } catch {
      // 用户取消或对话框不可用
    } finally {
      setPickingState(false)
    }
  }

  if (workspace.isLoading) {
    return (
      <SettingsSection title="全局工作目录" description="加载工作目录配置…">
        <Skeleton className="h-20 w-full" />
      </SettingsSection>
    )
  }

  if (!workspace.data) return null
  const info = workspace.data

  return (
    <SettingsSection
      title="全局工作目录"
      description="Agent 创建和操作的代码仓库都存放在此目录下。修改后新任务将使用新路径。"
      contentClassName="workspace-settings-panel overflow-hidden rounded-lg border"
    >
      <div className="workspace-settings-content space-y-4 p-4 sm:p-5">
        {/* 当前路径展示 */}
        <div className="workspace-root-block space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="workspace-folder-mark grid size-7 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
                <FolderIcon className="size-4" weight="fill" />
              </span>
              <span className="truncate font-medium">工作区根目录</span>
            </div>
            <span className="hidden text-[0.6875rem] text-muted-foreground sm:inline">
              新任务默认使用
            </span>
          </div>
          <div className="workspace-root-path group flex items-center gap-3 rounded-md border border-border bg-muted/35 px-3 py-2.5">
            <code className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
              {info.workspace_root}
            </code>
            {!editing && (
              <Button
                variant="ghost"
                size="sm"
                className="workspace-edit-button h-7 shrink-0 gap-1.5 px-2 text-xs"
                onClick={() => {
                  setDraftPath(info.workspace_root)
                  setEditing(true)
                }}
                aria-label="修改全局工作目录"
              >
                <PencilIcon className="size-3.5 transition-transform duration-150 group-hover:rotate-[-8deg]" />
                修改
              </Button>
            )}
          </div>
        </div>

        {/* 迁移进度提示 */}
        {migrationProgress && (
          <div className="workspace-migration-alert animate-in space-y-2 rounded-md border border-primary/30 bg-primary/5 p-3 fade-in-0 slide-in-from-top-2 duration-200">
            <div className="flex items-center gap-2">
              <CheckCircleIcon className="size-4 text-primary" weight="fill" />
              <span className="text-sm font-medium">工作区迁移完成</span>
            </div>
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>已迁移 {migrationProgress.migrated} 个项目到新工作区</p>
              {migrationProgress.errors.length > 0 && (
                <div className="space-y-0.5">
                  <p className="font-medium text-destructive">以下项目迁移失败：</p>
                  <ul className="list-inside list-disc space-y-0.5">
                    {migrationProgress.errors.map((error, index) => (
                      <li key={index}>{error}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setMigrationProgress(null)}
            >
              关闭
            </Button>
          </div>
        )}

        {/* 编辑模式 */}
        {editing && (
          <div className="workspace-edit-form animate-in space-y-3 rounded-md border border-primary/30 bg-primary/5 p-3 duration-200 fade-in-0 slide-in-from-top-2">
            <div className="space-y-1">
              <label className="text-xs font-medium">新路径</label>
              <div className="flex items-center gap-2">
                <Input
                  value={draftPath}
                  onChange={(e) => setDraftPath(e.target.value)}
                  placeholder="例如：E:\ai_workspace 或 /home/user/ai_workspace"
                  autoFocus
                  className={cn(
                    draftPath.trim() && !pathValidation.valid && "border-destructive focus-visible:ring-destructive/30"
                  )}
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={picking}
                  onClick={() => handlePickFolder(setDraftPath, setPicking)}
                  className={cn(
                    "workspace-browse-btn h-9 shrink-0 gap-1.5 px-3",
                    picking && "workspace-browse-btn--picking"
                  )}
                  title="浏览文件夹"
                >
                  <FolderOpenIcon
                    className={cn(
                      "size-4 transition-transform duration-200",
                      picking && "animate-[browse-pulse_1.2s_ease-in-out_infinite]"
                    )}
                    weight={picking ? "fill" : "regular"}
                  />
                  {picking ? "选择中…" : "浏览"}
                </Button>
              </div>
              {draftPath.trim() && !pathValidation.valid && (
                <p className="workspace-cdrive-error animate-in text-xs text-destructive fade-in-0 slide-in-from-top-1 duration-150">
                  {pathValidation.error}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={
                  update.isPending ||
                  !draftPath.trim() ||
                  !pathValidation.valid
                }
                onClick={() =>
                  update.mutate({ workspace_root: draftPath.trim() })
                }
              >
                {update.isPending ? "保存中…" : "保存"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={update.isPending}
                onClick={() => setEditing(false)}
              >
                取消
              </Button>
              {update.isError && (
                <span className="text-xs text-destructive">
                  {(update.error as Error)?.message ?? "保存失败"}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 派生目录 */}
        <div className="workspace-derived-grid grid gap-3 sm:grid-cols-2">
          {/* 项目目录卡片 */}
          <div className="workspace-derived-card space-y-2 rounded-md border border-border bg-card px-3 py-2.5">
            <p className="text-xs font-medium text-muted-foreground">
              项目目录
            </p>
            {!editingProjects ? (
              <div className="group flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate text-xs text-foreground/90">
                  {info.projects_dir}
                </code>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 shrink-0 gap-1 px-1.5 text-[0.6875rem] opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => {
                    setDraftProjects(info.projects_dir)
                    setEditingProjects(true)
                  }}
                >
                  <PencilIcon className="size-3" />
                  修改
                </Button>
              </div>
            ) : (
              <div className="animate-in space-y-2 rounded-md border border-primary/20 bg-primary/5 p-2 fade-in-0 slide-in-from-top-1 duration-150">
                <div className="flex items-center gap-1.5">
                  <Input
                    value={draftProjects}
                    onChange={(e) => setDraftProjects(e.target.value)}
                    placeholder="例如：E:\ai_workspace\projects 或 /home/user/projects"
                    autoFocus
                    className={cn(
                      "h-7 text-xs",
                      draftProjects.trim() && !projectsValidation.valid && "border-destructive focus-visible:ring-destructive/30"
                    )}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={pickingProjects}
                    onClick={() => handlePickFolder(setDraftProjects, setPickingProjects)}
                    className={cn(
                      "workspace-browse-btn h-7 shrink-0 gap-1 px-2 text-xs",
                      pickingProjects && "workspace-browse-btn--picking"
                    )}
                    title="浏览文件夹"
                  >
                    <FolderOpenIcon
                      className={cn(
                        "size-3.5 transition-transform duration-200",
                        pickingProjects && "animate-[browse-pulse_1.2s_ease-in-out_infinite]"
                      )}
                      weight={pickingProjects ? "fill" : "regular"}
                    />
                  </Button>
                </div>
                {draftProjects.trim() && !projectsValidation.valid && (
                  <p className="workspace-cdrive-error animate-in text-[0.6875rem] text-destructive fade-in-0 duration-150">
                    {projectsValidation.error}
                  </p>
                )}
                <div className="flex items-center gap-1.5">
                  <Button
                    size="sm"
                    className="h-6 text-xs"
                    disabled={
                      update.isPending ||
                      !draftProjects.trim() ||
                      !projectsValidation.valid
                    }
                    onClick={() =>
                      update.mutate({ projects_dir: draftProjects.trim() })
                    }
                  >
                    {update.isPending ? "保存中…" : "保存"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    disabled={update.isPending}
                    onClick={() => setEditingProjects(false)}
                  >
                    取消
                  </Button>
                  {update.isError && (
                    <span className="text-[0.6875rem] text-destructive">
                      {(update.error as Error)?.message ?? "保存失败"}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* 技能目录卡片 */}
          <div className="workspace-derived-card space-y-2 rounded-md border border-border bg-card px-3 py-2.5">
            <p className="text-xs font-medium text-muted-foreground">
              技能目录
            </p>
            {!editingSkills ? (
              <div className="group flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate text-xs text-foreground/90">
                  {info.skills_dir}
                </code>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 shrink-0 gap-1 px-1.5 text-[0.6875rem] opacity-0 transition-opacity group-hover:opacity-100"
                  onClick={() => {
                    setDraftSkills(info.skills_dir)
                    setEditingSkills(true)
                  }}
                >
                  <PencilIcon className="size-3" />
                  修改
                </Button>
              </div>
            ) : (
              <div className="animate-in space-y-2 rounded-md border border-primary/20 bg-primary/5 p-2 fade-in-0 slide-in-from-top-1 duration-150">
                <div className="flex items-center gap-1.5">
                  <Input
                    value={draftSkills}
                    onChange={(e) => setDraftSkills(e.target.value)}
                    placeholder="例如：E:\ai_workspace\skills 或 /home/user/skills"
                    autoFocus
                    className={cn(
                      "h-7 text-xs",
                      draftSkills.trim() && !skillsValidation.valid && "border-destructive focus-visible:ring-destructive/30"
                    )}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={pickingSkills}
                    onClick={() => handlePickFolder(setDraftSkills, setPickingSkills)}
                    className={cn(
                      "workspace-browse-btn h-7 shrink-0 gap-1 px-2 text-xs",
                      pickingSkills && "workspace-browse-btn--picking"
                    )}
                    title="浏览文件夹"
                  >
                    <FolderOpenIcon
                      className={cn(
                        "size-3.5 transition-transform duration-200",
                        pickingSkills && "animate-[browse-pulse_1.2s_ease-in-out_infinite]"
                      )}
                      weight={pickingSkills ? "fill" : "regular"}
                    />
                  </Button>
                </div>
                {draftSkills.trim() && !skillsValidation.valid && (
                  <p className="workspace-cdrive-error animate-in text-[0.6875rem] text-destructive fade-in-0 duration-150">
                    {skillsValidation.error}
                  </p>
                )}
                <div className="flex items-center gap-1.5">
                  <Button
                    size="sm"
                    className="h-6 text-xs"
                    disabled={
                      update.isPending ||
                      !draftSkills.trim() ||
                      !skillsValidation.valid
                    }
                    onClick={() =>
                      update.mutate({ skills_dir: draftSkills.trim() })
                    }
                  >
                    {update.isPending ? "保存中…" : "保存"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs"
                    disabled={update.isPending}
                    onClick={() => setEditingSkills(false)}
                  >
                    取消
                  </Button>
                  {update.isError && (
                    <span className="text-[0.6875rem] text-destructive">
                      {(update.error as Error)?.message ?? "保存失败"}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
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
  if (!session.data) {
    return (
      <main className="p-6">
        <p className="text-sm text-muted-foreground">读取会话信息失败，请刷新页面重试。</p>
      </main>
    )
  }

  return (
    <AppShell user={session.data} title="个人设置">
      <WorkspaceSection />
      <CustomModelsSection />
      <CommandApprovalSection />
      <SkillRetrievalSection />
    </AppShell>
  )
}
