/**
 * Diagonal-hatch strip footer of the ink race hero (prototype
 * `BActivityRace`). No photo data exists — it's a decorative band carrying
 * real pace/cadence/RPE chips passed by the caller (literal-copy: structure
 * reproduced, contents bound to real fields).
 */
export default function PhotoStrip({ items }: { items: string[] }) {
  return (
    <div
      className="flex h-[54px] items-center justify-around text-[11px] uppercase tracking-[0.6px] text-white/45"
      style={{
        background:
          'repeating-linear-gradient(135deg, rgba(255,255,255,0.04) 0 12px, rgba(255,255,255,0.08) 12px 24px)',
      }}
    >
      {items.map((it, i) => (
        <span key={i}>{it}</span>
      ))}
    </div>
  )
}
