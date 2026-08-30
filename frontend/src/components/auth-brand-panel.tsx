import { Check } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";

const FEATURES = [
  "Sign in with Google, Microsoft or email",
  "Per-workspace access for each client team",
  "Human-in-the-loop on every send",
];

/** Abstract "talent network" line illustration — translucent, no solid background. */
function HeroArt() {
  const nodes: [number, number, number][] = [
    [120, 90, 5], [210, 140, 6], [300, 80, 5], [450, 90, 5], [160, 220, 6],
    [350, 250, 6], [440, 210, 6], [110, 320, 5], [220, 330, 7], [320, 340, 6],
    [420, 330, 6], [180, 420, 6], [400, 430, 5],
  ];
  const lines: [number, number, number, number][] = [
    [120, 90, 210, 140], [210, 140, 300, 80], [300, 80, 380, 150], [380, 150, 450, 90],
    [210, 140, 160, 220], [160, 220, 260, 210], [260, 210, 380, 150], [260, 210, 350, 250],
    [350, 250, 440, 210], [380, 150, 440, 210], [160, 220, 110, 320], [260, 210, 220, 330],
    [220, 330, 320, 340], [320, 340, 350, 250], [320, 340, 420, 330], [420, 330, 440, 210],
    [110, 320, 220, 330], [220, 330, 180, 420], [320, 340, 400, 430], [400, 430, 420, 330],
  ];
  const matched: [number, number][] = [[380, 150], [260, 210], [320, 340]];

  return (
    <svg
      viewBox="0 0 520 520"
      preserveAspectRatio="xMidYMid slice"
      className="pointer-events-none absolute inset-0 h-full w-full opacity-90"
      aria-hidden
    >
      {[130, 200, 270].map((r) => (
        <circle key={r} cx="320" cy="220" r={r} fill="none" stroke="white" strokeOpacity="0.05" />
      ))}
      {lines.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="white" strokeOpacity="0.13" />
      ))}
      {nodes.map(([cx, cy, r], i) => (
        <circle key={i} cx={cx} cy={cy} r={r} fill="white" fillOpacity="0.06" stroke="white" strokeOpacity="0.28" />
      ))}
      {matched.map(([cx, cy], i) => (
        <g key={i}>
          <circle cx={cx} cy={cy} r="14" fill="none" stroke="#43B68F" strokeOpacity="0.4" strokeWidth="1.5" />
          <circle cx={cx} cy={cy} r="9" fill="#43B68F" />
          <path
            d={`M${cx - 4} ${cy} l3 3 l5 -6`}
            stroke="#06241b"
            strokeWidth="2"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </g>
      ))}
    </svg>
  );
}

/** The gradient brand column shared by the sign-in and sign-up screens. */
export function AuthBrandPanel({ headline, blurb }: { headline: string; blurb: string }) {
  return (
    <div
      className="relative hidden flex-col overflow-hidden p-14 text-[#EBF7F1] lg:flex"
      style={{
        background:
          "linear-gradient(150deg, var(--sidebar), var(--sidebar-active) 58%, var(--score-from))",
      }}
    >
      <HeroArt />
      <div className="relative z-10 flex items-center gap-2.5 font-display text-2xl font-bold">
        <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-score-from to-score-to text-primary-foreground">
          <BrandMark className="size-5" />
        </span>
        Sourcewell
      </div>
      <h1 className="relative z-10 mt-auto max-w-[11ch] font-display text-[2.6rem] font-bold leading-[1.08] tracking-tight">
        {headline}
      </h1>
      <p className="relative z-10 mt-5 max-w-[42ch] text-[15px] leading-relaxed text-[#A9D4C8]">
        {blurb}
      </p>
      <div className="relative z-10 mt-8 flex flex-col gap-3">
        {FEATURES.map((f) => (
          <div key={f} className="flex items-center gap-3 text-sm text-[#CDE8DF]">
            <span className="grid size-5 place-items-center rounded-md bg-[#0E7C66]">
              <Check className="size-3" strokeWidth={3} />
            </span>
            {f}
          </div>
        ))}
      </div>
    </div>
  );
}
