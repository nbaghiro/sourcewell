/** Sourcewell brand mark — the "Locate" reticle. Inherits color via currentColor. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.3" strokeOpacity="0.34" />
      <circle cx="12" cy="12" r="5.6" stroke="currentColor" strokeWidth="1.7" strokeOpacity="0.75" />
      <circle cx="12" cy="12" r="2" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <path d="M12 1.4V4M12 20v2.6M1.4 12H4M20 12h2.6" />
      </g>
    </svg>
  );
}
