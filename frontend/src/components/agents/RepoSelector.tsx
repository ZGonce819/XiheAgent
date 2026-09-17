import { CaretDownIcon } from "@phosphor-icons/react"
import { GitBranch } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { detectRepoProvider, normalizeRepoFullName, type RepoProvider } from "@/lib/repo"
import { cn } from "@/lib/utils"

type RepoOption = { full_name: string }

interface RepoSelectorProps {
  repos?: Array<RepoOption>
  selectedRepo?: string | null
  onRepoChange: (repo: string | null) => void
  placeholder?: string
  emptySelectionLabel?: string
  searchPlaceholder?: string
  noMatchesLabel?: string
  className?: string
  triggerClassName?: string
  dropdownClassName?: string
  disabled?: boolean
  onProviderChange?: (provider: RepoProvider) => void
}

export function RepoSelector({
  repos,
  selectedRepo = null,
  onRepoChange,
  placeholder = "选择仓库",
  emptySelectionLabel = "不指定仓库",
  noMatchesLabel = "暂无匹配仓库",
  className,
  triggerClassName,
  dropdownClassName,
  disabled = false,
  onProviderChange,
}: RepoSelectorProps) {
  const [open, setOpen] = useState(false)
  const [owner, setOwner] = useState("")
  const [repoName, setRepoName] = useState("")
  const [provider, setProvider] = useState<RepoProvider>("github")
  const [validationError, setValidationError] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const ownerRef = useRef<HTMLInputElement>(null)
  const repoRef = useRef<HTMLInputElement>(null)

  const query = owner || repoName ? `${owner}/${repoName}`.replace(/^\/|\/$/g, "").replace(/\/+/g, "/") : ""

  const filteredRepos = useMemo(() => {
    const all = repos ?? []
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter((repo) => repo.full_name.toLowerCase().includes(q))
  }, [repos, query])

  const customRepo = useMemo(() => {
    const trimmed = query.trim()
    if (!trimmed) return null
    const normalized = normalizeRepoFullName(trimmed)
    if (!normalized) return null
    const exists = (repos ?? []).some((repo) => repo.full_name === normalized)
    if (exists) return null
    if (detectRepoProvider(trimmed)) return trimmed
    const parts = normalized.split("/")
    if (parts.length === 2) {
      const host = provider === "github" ? "https://github.com" : provider === "codeup" ? "https://codeup.aliyun.com" : "https://gitee.com"
      return `${host}/${normalized}`
    }
    return trimmed
  }, [query, repos, provider])

  const clearInputs = () => { setOwner(""); setRepoName(""); setValidationError(null) }

  // 触发器标签拆分为「前缀（协议/主机/owner）」与「仓库名」，用于分阶段截断：
  // 先前缀从前往后出前置省略号（保留 repo 前的 /），再仓库名从后往前出尾部省略号
  const triggerParts = useMemo(() => {
    if (!selectedRepo) return null
    const idx = selectedRepo.lastIndexOf("/")
    return idx >= 0
      ? { prefix: selectedRepo.slice(0, idx), repo: selectedRepo.slice(idx + 1) }
      : { prefix: "", repo: selectedRepo }
  }, [selectedRepo])

  const handleEnterKey = useCallback(() => {
    if (!owner.trim() || !repoName.trim()) {
      setValidationError(!owner.trim() && !repoName.trim() ? "请填写 owner 和 repo" : !owner.trim() ? "请填写 owner" : "请填写 repo")
      return
    }
    setValidationError(null)
    const trimmed = `${owner.trim()}/${repoName.trim()}`
    const normalized = normalizeRepoFullName(trimmed)
    const existing = (repos ?? []).find((repo) => repo.full_name === normalized)
    if (existing) {
      onRepoChange(existing.full_name)
    } else if (customRepo) {
      onRepoChange(customRepo)
    } else {
      onRepoChange(trimmed)
    }
    setOpen(false)
    clearInputs()
  }, [owner, repoName, repos, customRepo, onRepoChange])

  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => ownerRef.current?.focus())
    }
  }, [open])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (dropdownRef.current && !dropdownRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  return (
    <div ref={dropdownRef} className={cn("relative min-w-0 shrink", className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setOpen((value) => {
            if (!value && selectedRepo) {
              const normalized = normalizeRepoFullName(selectedRepo)
              if (normalized) {
                const parts = normalized.split("/")
                if (parts.length === 2 && parts[0] && parts[1]) {
                  setOwner(parts[0])
                  setRepoName(parts[1])
                } else if (parts.length === 3 && parts[1] && parts[2]) {
                  setOwner(parts[1])
                  setRepoName(parts[2])
                }
              }
            }
            return !value
          })
        }}
        className={cn(
          "flex max-w-[300px] cursor-pointer items-center gap-1.5 rounded-md text-[0.8125rem] transition-[background-color,border-color,color,opacity,transform] duration-200 active:scale-[0.98] disabled:cursor-default disabled:opacity-60",
          selectedRepo
            ? // 已选仓库：与环境/项目选择器一致的胶囊背景，选中瞬间淡入+轻微缩放
              "animate-in fade-in-0 zoom-in-95 border border-[var(--ui-border)] bg-[var(--ui-panel-2)] px-2 py-1 ease-out text-[color:var(--ui-text-muted)] hover:border-[var(--ui-accent)] hover:text-[color:var(--ui-text)]"
            : "border-transparent px-0 py-1 text-muted-foreground hover:opacity-80",
          triggerClassName
        )}
      >
        <GitBranch className="size-3.5 shrink-0" />
        <span className="flex min-w-0 flex-1 items-center text-left">
          {triggerParts?.prefix ? (
            <>
              <span
                className="min-w-[1em] truncate"
                style={{ flexShrink: 10000, direction: "rtl" }}
              >
                {triggerParts.prefix}
              </span>
              <span className="shrink-0">/</span>
            </>
          ) : null}
          <span className="min-w-0 truncate" style={{ flexShrink: 1 }}>
            {triggerParts ? triggerParts.repo : placeholder}
          </span>
        </span>
        <CaretDownIcon className={cn("size-3 shrink-0 opacity-70 transition-transform duration-200 ease-out", open && "rotate-180")} />
      </button>
      {open && (
        <div
          className={cn(
            "absolute top-full left-0 z-50 mt-1 flex max-h-96 w-[22rem] origin-top-left animate-in fade-in-0 zoom-in-95 slide-in-from-top-1 duration-150 ease-out flex-col overflow-hidden rounded-lg border border-border bg-popover text-[0.8125rem] text-popover-foreground shadow-lg",
            dropdownClassName
          )}
        >
          <div className="flex items-center gap-1 border-b border-border px-2 py-2">
            {(["github", "gitee", "codeup"] as RepoProvider[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setProvider(item)
                  onProviderChange?.(item)
                }}
                className={cn(
                  "rounded-md px-2 py-1 text-[0.75rem] font-medium transition-[background-color,color,transform] duration-150 active:scale-95",
                  provider === item
                    ? "bg-[var(--ui-accent)] text-white"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                {item === "github" ? "GitHub" : item === "gitee" ? "Gitee" : "Codeup"}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-0 border-b border-border px-3 py-2.5">
            <input
              ref={ownerRef}
              autoFocus
              value={owner}
              onChange={(e) => { setOwner(e.target.value.replace(/\//g, "")); setValidationError(null) }}
              onKeyDown={(e) => {
                if (e.key === "/" || e.key === "ArrowRight") { e.preventDefault(); repoRef.current?.focus() }
                else if (e.key === "Enter") { e.preventDefault(); handleEnterKey() }
              }}
              placeholder={validationError && !owner ? "请填写 owner" : "owner"}
              className={cn("w-24 bg-transparent text-foreground outline-none", validationError && !owner ? "placeholder:text-[color:var(--ui-danger)]" : "placeholder:text-muted-foreground")}
            />
            <span className="mx-1 rounded bg-muted px-1.5 py-0.5 text-[0.6875rem] font-medium text-muted-foreground select-none">/</span>
            <input
              ref={repoRef}
              value={repoName}
              onChange={(e) => { setRepoName(e.target.value.replace(/\//g, "")); setValidationError(null) }}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft" && !repoName) { e.preventDefault(); ownerRef.current?.focus() }
                else if (e.key === "Enter") { e.preventDefault(); handleEnterKey() }
              }}
              placeholder={validationError && !repoName ? "请填写 repo" : "repo"}
              className={cn("flex-1 min-w-0 bg-transparent text-foreground outline-none", validationError && !repoName ? "placeholder:text-[color:var(--ui-danger)]" : "placeholder:text-muted-foreground")}
            />
          </div>
          <div className="animate-in fade-in-0 duration-200 overflow-y-auto">
            <button
              type="button"
              onClick={() => {
                onRepoChange(null)
                setOpen(false)
                clearInputs()
              }}
              className={cn(
                "flex w-full items-center px-2 py-1.5 text-left transition-[background-color,color] duration-150 hover:bg-muted",
                selectedRepo ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {emptySelectionLabel}
              {!selectedRepo && (
                <span className="ml-auto animate-in fade-in-0 zoom-in-75 pl-3 text-muted-foreground duration-150">✓</span>
              )}
            </button>
            {filteredRepos.length > 0 && filteredRepos.map((repo) => {
                const selected = repo.full_name === selectedRepo
                return (
                  <button
                    key={repo.full_name}
                    type="button"
                    onClick={() => {
                      onRepoChange(repo.full_name)
                      setOpen(false)
                      clearInputs()
                    }}
                    className={cn(
                      "flex w-full items-center px-2 py-1.5 text-left transition-[background-color,color] duration-150 hover:bg-muted",
                      selected ? "text-foreground" : "text-muted-foreground"
                    )}
                  >
                    <span className="truncate">{repo.full_name}</span>
                    {selected && (
                        <span className="ml-auto animate-in fade-in-0 zoom-in-75 pl-3 text-muted-foreground duration-150">
                        ✓
                      </span>
                    )}
                  </button>
                )
            })}
            {filteredRepos.length === 0 && !customRepo ? (
              <p className="px-3 py-2 text-xs text-[color:var(--ui-text-dim)]">{noMatchesLabel}</p>
            ) : null}
            {customRepo && (
              <button
                type="button"
                onClick={() => {
                  onRepoChange(customRepo)
                  setOpen(false)
                  clearInputs()
                }}
                className="flex w-full items-center px-2 py-1.5 text-left text-foreground transition-[background-color,color] duration-150 hover:bg-muted"
              >
                <span className="min-w-0 truncate">使用此仓库：{customRepo}</span>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

