export function Logo({ className = "h-5" }: { className?: string }) {
  return (
    <span
      className={`font-heading text-sm font-semibold tracking-tight text-current ${className}`}
    >
      XiheAgent
    </span>
  );
}
