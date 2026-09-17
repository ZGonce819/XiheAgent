import {
  ArrowUp,
  ChevronDown,
  Cloud,
  LoaderCircle,
  Monitor,
  Square,
  X,
} from "lucide-react"
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"

import { useNavigate } from "@tanstack/react-router"

import type { ModelOption } from "@/lib/api"
import type { ImageChunk } from "@/lib/agents/types"
import type { ModelSelection } from "@/lib/agents/useModelOptions"
import type { RepoProvider } from "@/lib/repo"
import { RepoSelector } from "@/components/agents/RepoSelector"
import { ProjectSelector } from "@/components/agents/ProjectSelector"
import { formatModelSelection } from "@/lib/agents/useModelOptions"
import { cn } from "@/lib/utils"

const PROMPT_TEXTAREA_MAX_HEIGHT = 200
const MAX_IMAGE_COUNT = 5
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const SUPPORTED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
])

export interface CloudPromptBarProps {
  placeholder?: string
  compact?: boolean
  disabled?: boolean
  /** Shows a non-interactive close mark when the composer is unavailable in a read-only view. */
  readOnly?: boolean
  busy?: boolean
  onSubmit?: (value: string, images: Array<ImageChunk>) => boolean | void
  /** Called to stop the running agent. When set, the send button becomes a stop button while busy and the input is empty. */
  onStop?: () => void
  stopping?: boolean
  models?: Array<ModelOption>
  selection?: ModelSelection | null
  onSelectionChange?: (next: ModelSelection) => void
  /** Repos the user can target. When provided with onRepoChange, a repo picker is shown. */
  repos?: Array<{ full_name: string }>
  selectedRepo?: string | null
  onRepoChange?: (repo: string | null) => void
  /** Current backend type: "local" or "sandbox". */
  backendType?: string
  /** Called when user switches between local and sandbox. */
  onBackendTypeChange?: (backendType: string) => void
  /** Currently selected project name. */
  selectedProject?: string | null
  /** Called when user selects a different project. */
  onProjectChange?: (project: string | null) => void
}

function fileToImageChunk(file: File): Promise<ImageChunk | null> {
  if (!SUPPORTED_IMAGE_TYPES.has(file.type) || file.size > MAX_IMAGE_BYTES) {
    return Promise.resolve(null)
  }

  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : ""
      const base64 = dataUrl.split(",")[1]
      resolve(
        base64
          ? {
              kind: "image",
              base64,
              mimeType: file.type,
              fileName: file.name,
            }
          : null
      )
    }
    reader.onerror = () => resolve(null)
    reader.readAsDataURL(file)
  })
}

/** Web-adapted PromptBar from open-swe-app - local state, no Electron/Zustand deps. */
export const CloudPromptBar = memo(function CloudPromptBarComponent({
  placeholder = "",
  compact = false,
  disabled = false,
  readOnly = false,
  busy = false,
  onSubmit,
  onStop,
  stopping = false,
  models = [],
  selection = null,
  onSelectionChange,
  repos,
  selectedRepo = null,
  onRepoChange,
  backendType = "local",
  onBackendTypeChange,
  selectedProject,
  onProjectChange,
}: CloudPromptBarProps) {
  const navigate = useNavigate()
  const [value, setValue] = useState("")
  const [pendingImages, setPendingImages] = useState<Array<ImageChunk>>([])
  const [isDragOver, setIsDragOver] = useState(false)
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false)
  const [repoProvider, setRepoProvider] = useState<RepoProvider>("github")
  const [backendDropdownOpen, setBackendDropdownOpen] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dragDepthRef = useRef(0)
  const modelDropdownRef = useRef<HTMLDivElement>(null)
  const backendDropdownRef = useRef<HTMLDivElement>(null)

  const dynamicPlaceholder = useMemo(
    () =>
      `让 XiheAgent 构建、修复或检查一个 ${
        repoProvider === "github"
          ? "GitHub"
          : repoProvider === "codeup"
            ? "Codeup"
            : "Gitee"
      } 仓库`,
    [repoProvider]
  )

  const combos = useMemo<Array<ModelSelection>>(() => {
    const list: Array<ModelSelection> = []
    for (const model of models) {
      for (const effort of model.efforts) {
        list.push({ modelId: model.id, effort })
      }
    }
    return list
  }, [models])

  const selectionLabel = formatModelSelection(models, selection)

  const canSubmit =
    !disabled && (value.trim().length > 0 || pendingImages.length > 0)

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim()
    if (!canSubmit) return
    const result = onSubmit?.(trimmed, pendingImages)
    // onSubmit 返回 false 时不清空输入框（如校验未通过）
    if (result !== false) {
      setValue("")
      setPendingImages([])
    }
  }, [canSubmit, onSubmit, pendingImages, value])

  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return

    el.style.height = "auto"
    const clampedHeight = Math.min(el.scrollHeight, PROMPT_TEXTAREA_MAX_HEIGHT)
    el.style.height = `${clampedHeight}px`
    el.style.overflowY =
      el.scrollHeight > PROMPT_TEXTAREA_MAX_HEIGHT ? "auto" : "hidden"
  }, [value])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (
        modelDropdownRef.current &&
        !modelDropdownRef.current.contains(target)
      ) {
        setModelDropdownOpen(false)
      }
      if (
        backendDropdownRef.current &&
        !backendDropdownRef.current.contains(target)
      ) {
        setBackendDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const addFiles = useCallback(async (files: FileList | Array<File>) => {
    const nextImages = await Promise.all(
      Array.from(files).map(fileToImageChunk)
    )
    const validImages = nextImages.filter(
      (image): image is ImageChunk => image !== null
    )
    if (validImages.length === 0) return
    setPendingImages((prev) =>
      [...prev, ...validImages].slice(0, MAX_IMAGE_COUNT)
    )
  }, [])

  const handleDragEnter = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragDepthRef.current += 1
    setIsDragOver(true)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) setIsDragOver(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      if (!e.dataTransfer.types.includes("Files")) return
      e.preventDefault()
      dragDepthRef.current = 0
      setIsDragOver(false)
      void addFiles(e.dataTransfer.files)
    },
    [addFiles]
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && canSubmit) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const pickerDisabled = combos.length === 0 || !onSelectionChange
  const showStop =
    busy && !disabled && !value.trim() && pendingImages.length === 0 && !!onStop

  return (
    <div
      className={cn(
        "relative w-full font-sans text-[0.8125rem]",
        compact ? "max-w-none" : "max-w-2xl"
      )}
    >
      {onRepoChange && (
        <div className="mb-2 flex items-center gap-2 px-1 text-xs">
          <RepoSelector
            repos={repos}
            selectedRepo={selectedRepo}
            onRepoChange={onRepoChange}
            onProviderChange={setRepoProvider}
          />
          {onBackendTypeChange && (
            <>
              <div ref={backendDropdownRef} className="relative">
              <button
                type="button"
                onClick={() => setBackendDropdownOpen((open) => !open)}
                className="flex items-center gap-1 rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel-2)] px-2 py-1 text-[0.75rem] text-[color:var(--ui-text-muted)] transition-colors hover:border-[var(--ui-accent)] hover:text-[color:var(--ui-text)]"
              >
                <>
                  {backendType === "sandbox" ? <Cloud className="size-3" /> : <Monitor className="size-3" />}
                  <span>{backendType === "sandbox" ? "沙箱环境" : "本地环境"}</span>
                  <ChevronDown className={cn("size-3 transition-transform duration-200", backendDropdownOpen && "rotate-180")} />
                </>
              </button>
              {backendDropdownOpen && (
                <div className="absolute left-0 top-full z-50 mt-1 min-w-[160px] origin-top-left animate-in fade-in-0 zoom-in-95 slide-in-from-top-1 duration-150 ease-out overflow-hidden rounded border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                  <button
                    type="button"
                    onClick={() => {
                      onBackendTypeChange("local")
                      setBackendDropdownOpen(false)
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--ui-panel-2)]",
                      backendType === "local"
                        ? "text-[color:var(--ui-text)]"
                        : "text-[color:var(--ui-text-muted)]"
                    )}
                  >
                    <Monitor className="size-3" />
                    本地环境
                    {backendType === "local" && <span className="ml-auto text-[color:var(--ui-text-dim)]">✓</span>}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onBackendTypeChange("sandbox")
                      setBackendDropdownOpen(false)
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--ui-panel-2)]",
                      backendType === "sandbox"
                        ? "text-[color:var(--ui-text)]"
                        : "text-[color:var(--ui-text-muted)]"
                    )}
                  >
                    <Cloud className="size-3" />
                    沙箱环境
                    {backendType === "sandbox" && <span className="ml-auto text-[color:var(--ui-text-dim)]">✓</span>}
                  </button>
                </div>
              )}
            </div>
              <ProjectSelector
                selectedProject={selectedProject}
                onProjectChange={onProjectChange}
                backendType={backendType}
              />
            </>
          )}
        </div>
      )}
      <div
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={cn(
        "relative flex min-h-[116px] flex-col rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] px-4 py-3 shadow-sm transition-[border-color,box-shadow] duration-150 focus-within:border-[var(--ui-accent)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklab,var(--ui-accent)_12%,transparent)]",
          compact && "min-h-[88px]",
          isDragOver && "border-[var(--ui-accent)]"
        )}
      >
        {isDragOver && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-[var(--ui-surface)]/90">
            <span className="rounded-md bg-[var(--ui-panel-2)] px-3 py-1.5 text-sm font-medium text-[color:var(--ui-accent)]">
              在此放置图片
            </span>
          </div>
        )}

        {pendingImages.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {pendingImages.map((image, index) => (
              <div
                key={`${image.fileName ?? "image"}-${index}`}
                className="group relative"
              >
                <img
                  src={`data:${image.mimeType};base64,${image.base64}`}
                  alt={image.fileName || "待发送图片"}
                  className="size-16 rounded-lg border border-[var(--ui-border)] object-cover"
                />
                <button
                  type="button"
                  aria-label="移除图片"
                  onClick={() =>
                    setPendingImages((prev) =>
                      prev.filter((_, i) => i !== index)
                    )
                  }
                  className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full border border-[var(--ui-border)] bg-[var(--ui-panel-2)] text-[color:var(--ui-text-muted)] opacity-0 shadow-sm transition-opacity group-hover:opacity-100 hover:text-[color:var(--ui-text)]"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={inputRef}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={busy ? "输入下一条指令，将在当前任务后执行" : (placeholder || dynamicPlaceholder)}
          disabled={disabled}
          className={cn(
            "w-full min-w-0 resize-none overflow-hidden bg-transparent text-[0.875rem] leading-6 text-[color:var(--ui-text)] outline-none placeholder:text-[color:var(--ui-text-dim)]",
            compact ? "min-h-[36px]" : "min-h-[52px]"
          )}
          style={{ maxHeight: PROMPT_TEXTAREA_MAX_HEIGHT }}
        />

        <div className="mt-auto flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 pt-2 text-xs text-[color:var(--ui-text-dim)]">
          <div ref={modelDropdownRef} className="relative min-w-0 shrink">
            <button
              type="button"
              disabled={pickerDisabled}
              onClick={() => setModelDropdownOpen((open) => !open)}
              className="flex max-w-[220px] cursor-pointer items-center gap-0.5 text-[0.8125rem] text-[color:var(--ui-text-muted)] transition-[color,opacity,transform] duration-150 hover:opacity-80 active:scale-[0.98] disabled:cursor-default disabled:opacity-60"
            >
              <span className="truncate">{selectionLabel}</span>
              {!pickerDisabled && (
                <ChevronDown className={cn("size-3.5 shrink-0 opacity-60 transition-transform duration-200 ease-out", modelDropdownOpen && "rotate-180")} />
              )}
            </button>
            {modelDropdownOpen && combos.length > 0 && (
              <div className="absolute bottom-full left-0 z-50 mb-1 max-h-72 origin-bottom-left animate-in fade-in-0 zoom-in-95 slide-in-from-bottom-1 duration-150 ease-out overflow-hidden flex flex-col rounded-lg border border-[var(--ui-border)] bg-[var(--ui-surface)] shadow-lg">
                <div className="flex-1 overflow-y-auto">
                  {combos.map((combo) => {
                    const selected =
                      !!selection &&
                      selection.modelId === combo.modelId &&
                      selection.effort === combo.effort
                    return (
                      <button
                        key={`${combo.modelId}::${combo.effort}`}
                        type="button"
                        onClick={() => {
                          onSelectionChange?.(combo)
                          setModelDropdownOpen(false)
                        }}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-1.5 text-left whitespace-nowrap transition-colors hover:bg-[var(--ui-panel-2)]",
                          selected
                            ? "text-[color:var(--ui-text)]"
                            : "text-[color:var(--ui-text-muted)]"
                        )}
                      >
                        {formatModelSelection(models, combo)}
                        {selected && (
                          <span className="ml-auto animate-in fade-in-0 zoom-in-75 pl-3 text-[color:var(--ui-text-dim)] duration-150">
                            ✓
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
                <div className="shrink-0 border-t border-[var(--ui-border)]">
                  <button
                    type="button"
                    onClick={() => {
                      setModelDropdownOpen(false)
                      navigate({
                        to: "/my-settings",
                        state: (previous) => ({ ...previous, addModel: true } as typeof previous),
                      })
                    }}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[color:var(--ui-accent)] transition-colors hover:bg-[var(--ui-panel-2)]"
                  >
                    自定义模型
                  </button>
                </div>
              </div>
            )}
          </div>

          {showStop ? (
            <button
              type="button"
              onClick={onStop}
              disabled={stopping}
              aria-label="停止任务"
              title="停止任务"
              className="ml-auto flex size-[32px] shrink-0 items-center justify-center rounded-full bg-[var(--ui-accent)] text-white shadow-sm transition-[opacity,transform,box-shadow] duration-200 hover:shadow-md hover:opacity-90 active:scale-95 disabled:cursor-default disabled:opacity-40"
            >
              {stopping ? (
                <LoaderCircle className="agents-spinner size-3.5" />
              ) : (
                <Square className="size-3 fill-current" strokeWidth={0} />
              )}
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!canSubmit}
              aria-label="发送消息"
              title="发送"
              className="ml-auto flex size-[32px] shrink-0 items-center justify-center rounded-full bg-[var(--ui-accent)] text-white shadow-sm transition-[opacity,transform,box-shadow] duration-200 hover:shadow-md hover:opacity-90 active:scale-95 disabled:cursor-default disabled:opacity-40"
            >
              {readOnly ? (
                <X className="size-3.5" strokeWidth={2.5} />
              ) : disabled ? (
                <LoaderCircle className="agents-spinner size-3.5" />
              ) : (
                <ArrowUp className="size-3.5" strokeWidth={2.5} />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  )
})

