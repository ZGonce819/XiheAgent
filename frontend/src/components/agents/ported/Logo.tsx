export function Logo({ className = "h-5" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 font-heading text-sm font-semibold text-current ${className}`}
    >
      <span className="inline-grid size-5 place-items-center border border-current/20 bg-current/[0.04] text-[0.625rem] leading-none">
        羲
      </span>
      <span>XiheAgent</span>
    </span>
  );
}
