import { useCallback, useEffect, useRef, useState } from "react"
import { SidebarSimpleIcon } from "@phosphor-icons/react"

import { cn } from "@/lib/utils"

const STORAGE_WIDTH = "open-swe.sidebar.width"
const STORAGE_COLLAPSED = "open-swe.sidebar.collapsed"

export const SIDEBAR_DEFAULT_WIDTH = 260
export const SIDEBAR_MIN_WIDTH = 200
export const SIDEBAR_MAX_WIDTH = 420

function readStoredWidth(): number {
  if (typeof window === "undefined") return SIDEBAR_DEFAULT_WIDTH
  const raw = window.localStorage.getItem(STORAGE_WIDTH)
  const parsed = raw ? Number(raw) : NaN
  if (!Number.isFinite(parsed)) return SIDEBAR_DEFAULT_WIDTH
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, parsed))
}

function readStoredCollapsed(): boolean {
  if (typeof window === "undefined") return false
  // On mobile the sidebar is a full-screen overlay, so start collapsed to keep
  // the chat visible regardless of the stored desktop preference.
  if (window.matchMedia("(max-width: 767px)").matches) return true
  return window.localStorage.getItem(STORAGE_COLLAPSED) === "1"
}

export function useSidebarLayout() {
  const [width, setWidthState] = useState<number>(() => readStoredWidth())
  const [collapsed, setCollapsedState] = useState<boolean>(() =>
    readStoredCollapsed()
  )

  const setWidth = useCallback((next: number) => {
    const clamped = Math.min(
      SIDEBAR_MAX_WIDTH,
      Math.max(SIDEBAR_MIN_WIDTH, next)
    )
    setWidthState(clamped)
    window.localStorage.setItem(STORAGE_WIDTH, String(clamped))
  }, [])

  const setCollapsed = useCallback((next: boolean) => {
    setCollapsedState(next)
    window.localStorage.setItem(STORAGE_COLLAPSED, next ? "1" : "0")
  }, [])

  const toggle = useCallback(
    () => setCollapsed(!collapsed),
    [collapsed, setCollapsed]
  )

  const closeOnMobile = useCallback(() => {
    if (typeof window === "undefined") return
    // State-only: don't persist, so the desktop collapsed preference is preserved.
    if (window.matchMedia("(max-width: 767px)").matches) setCollapsedState(true)
  }, [])

  return { width, collapsed, setWidth, setCollapsed, toggle, closeOnMobile }
}

interface SidebarFrameProps {
  width: number
  setWidth: (next: number) => void
  collapsed: boolean
  toggle: () => void
  className?: string
  children: React.ReactNode
}

export function SidebarFrame({
  width,
  setWidth,
  collapsed,
  toggle,
  className,
  children,
}: SidebarFrameProps) {
  const collapsedWidth = 48
  const currentWidth = collapsed ? collapsedWidth : width

  return (
    <aside
      style={{ width: `${currentWidth / 16}rem` }}
      className={cn(
        "relative flex h-svh shrink-0 flex-col transition-[width] duration-200 ease-out",
        "max-md:fixed max-md:inset-0 max-md:z-40 max-md:!w-full max-md:transition-none",
        className
      )}
    >
      <div
        className={cn(
          "flex h-full w-full flex-col overflow-hidden",
          collapsed ? "pointer-events-none opacity-0" : "opacity-100",
          "transition-opacity duration-150 ease-out"
        )}
      >
        {children}
      </div>
      <div className="max-md:hidden">
        <ResizeHandle width={width} onResize={setWidth} />
      </div>
      {collapsed && (
        <div
          className={cn(
            "absolute inset-0 flex items-start justify-center",
            "max-md:pointer-events-none max-md:w-0 max-md:bg-transparent"
          )}
        >
          <button
            type="button"
            aria-label="展开侧栏"
            onClick={toggle}
            className="group pointer-events-auto mt-3 inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground shadow-sm transition-[transform,background-color,color,box-shadow] duration-200 ease-out hover:scale-105 hover:bg-accent hover:text-foreground hover:shadow-md active:scale-95 max-md:fixed max-md:top-3 max-md:left-3 max-md:mt-0"
          >
            <SidebarSimpleIcon className="size-4 transition-transform duration-200 ease-out group-hover:scale-110" />
          </button>
        </div>
      )}
    </aside>
  )
}

function ResizeHandle({
  width,
  onResize,
}: {
  width: number
  onResize: (next: number) => void
}) {
  const startRef = useRef<{ x: number; width: number; scale: number } | null>(
    null
  )
  const [dragging, setDragging] = useState(false)

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const scale =
      parseFloat(getComputedStyle(document.documentElement).fontSize) / 16
    startRef.current = { x: e.clientX, width, scale }
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!startRef.current) return
    const next =
      startRef.current.width +
      (e.clientX - startRef.current.x) / startRef.current.scale
    onResize(next)
  }

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    startRef.current = null
    setDragging(false)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId)
    }
  }

  useEffect(() => {
    if (!dragging) return
    const prev = document.body.style.cursor
    document.body.style.cursor = "col-resize"
    return () => {
      document.body.style.cursor = prev
    }
  }, [dragging])

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      className={cn(
        "absolute top-0 right-0 z-20 h-full w-1 cursor-col-resize touch-none select-none",
        "after:absolute after:inset-y-0 after:right-0 after:w-px after:bg-transparent after:transition-colors",
        "hover:after:bg-border",
        dragging && "after:bg-border"
      )}
    />
  )
}

interface SidebarCollapseButtonProps {
  onToggle: () => void
  className?: string
}

export function SidebarCollapseButton({
  onToggle,
  className,
}: SidebarCollapseButtonProps) {
  return (
    <button
      type="button"
      aria-label="收起侧栏"
      onClick={onToggle}
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground",
        className
      )}
    >
      <SidebarSimpleIcon className="size-4" />
    </button>
  )
}
