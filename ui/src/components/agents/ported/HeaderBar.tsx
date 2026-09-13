interface HeaderBarProps {
  compact?: boolean;
}

export function HeaderBar({ compact }: HeaderBarProps) {
  if (compact) {
    return (
      <div className="flex items-center gap-3 px-3 py-2 font-sans text-xs border-b border-[var(--ui-border)] shrink-0">
        <span className="text-[var(--ui-text)] font-semibold">Xihe</span>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-4 px-4 py-3 font-sans text-sm">
      <span className="text-[var(--ui-text)] font-semibold">Xihe</span>
    </div>
  );
}
