import type { CSSProperties } from "react";

/** Placeholder sized by the text around it. Add `bone-fill` to stretch into a media box. */
export function Bone({ className = "", width }: { className?: string; width?: string }) {
  const style: CSSProperties | undefined = width ? { width } : undefined;
  return <span className={className ? `bone ${className}` : "bone"} style={style} aria-hidden="true" />;
}
