import { Link } from "@tanstack/react-router"
import { ArrowLeftIcon } from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type { SessionUser } from "@/lib/api"
import { AgentsSidebar } from "@/components/agents/AgentsSidebar"
import { cn } from "@/lib/utils"

interface AppShellProps {
  user: SessionUser
  title: string
  description?: string
  backTo?: { to: string; label: string }
  children: ReactNode
}

export function AppShell({
  user,
  title,
  description,
  backTo,
  children,
}: AppShellProps) {
  return (
    <div className="agents-ui flex h-svh overflow-hidden bg-background text-foreground">
      <AgentsSidebar user={user} />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl px-4 pt-14 pb-10 sm:px-8 sm:py-10">
          {backTo && (
            <Link
              to={backTo.to}
              className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeftIcon className="size-3.5" />
              {backTo.label}
            </Link>
          )}
          <header className="mb-8 border-b border-border pb-5">
            <h1 className="font-heading text-xl font-semibold">{title}</h1>
            {description && (
              <p className="mt-1 text-xs text-muted-foreground">
                {description}
              </p>
            )}
          </header>
          <div className="space-y-8">{children}</div>
        </div>
      </main>
    </div>
  )
}

interface SettingsSectionProps {
  title: string
  description?: string
  action?: ReactNode
  contentClassName?: string
  children: ReactNode
}

export function SettingsSection({
  title,
  description,
  action,
  contentClassName,
  children,
}: SettingsSectionProps) {
  return (
    <section className="space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {title}
          </h2>
          {description && (
            <p className="mt-0.5 text-xs text-muted-foreground/80">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      <div
        className={cn(
          "border-y border-border bg-card sm:border",
          contentClassName
        )}
      >
        {children}
      </div>
    </section>
  )
}

interface SettingsRowProps {
  label: string
  description?: string
  control: ReactNode
  htmlFor?: string
  comingSoon?: boolean
}

export function SettingsRow({
  label,
  description,
  control,
  htmlFor,
  comingSoon,
}: SettingsRowProps) {
  return (
    <div className="flex flex-col gap-2 border-b border-border px-4 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <label className="flex flex-col gap-0.5" htmlFor={htmlFor}>
        <span className="flex items-center gap-2">
          <span
            className={`text-xs font-medium ${
              comingSoon ? "text-muted-foreground" : "text-foreground"
            }`}
          >
            {label}
          </span>
          {comingSoon && (
            <span className="rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[0.625rem] font-normal text-muted-foreground">
              即将推出
            </span>
          )}
        </span>
        {description && (
          <span className="text-xs text-muted-foreground">{description}</span>
        )}
      </label>
      <div className={`sm:shrink-0 ${comingSoon ? "opacity-50" : ""}`}>
        {control}
      </div>
    </div>
  )
}
