import type { ReactNode } from 'react'

interface TopBarProps {
  title: string
  /** Right-aligned micro slot (date, count, status) — README §4. */
  right?: ReactNode
  /**
   * Desktop-only sub-line under the big title (prototype `BdShell`
   * header). Mobile keeps the compact bar — no subtitle there.
   */
  subtitle?: ReactNode
  /**
   * Leading 26×26 mark. Defaults to the real Endurai app icon
   * (`public/endurai-icon.png`, 7px radius) per prototype `BTopBar`; pass a
   * custom node only for a one-off override. Mobile only — the desktop
   * `BdShell` header is icon-less (the sidebar carries the brand).
   */
  icon?: ReactNode
  className?: string
}

/**
 * Halo top bar. Two faithful renderings, breakpoint-switched:
 *
 * - **Mobile** (`md:hidden`, prototype `BTopBar`): logo + 16px title left,
 *   micro label right. Padding 18/20/10.
 * - **Desktop** (`hidden md:flex`, prototype `BdShell` sticky header): no
 *   logo, 24px title + 13px subtitle left, right slot. Sticky to viewport
 *   top with a hairline bottom border; the block is `hidden md:flex`, so
 *   its `-mx-9 px-9` bleeds the bg/border across the full content canvas
 *   against the page's `md:px-9` gutter, re-padding to the prototype's 36px.
 *
 * Presentational only.
 */
export default function TopBar({ title, right, subtitle, icon, className = '' }: TopBarProps) {
  return (
    <>
      <header
        className={`flex items-center justify-between px-5 pt-[18px] pb-2.5 md:hidden ${className}`}
      >
        <div className="flex items-center gap-2.5">
          {icon ?? (
            <img
              src="/endurai-icon.png"
              alt=""
              aria-hidden="true"
              className="block h-[26px] w-[26px] rounded-[7px]"
            />
          )}
          <span className="text-base font-semibold text-halo-ink">{title}</span>
        </div>
        {right != null && (
          <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {right}
          </span>
        )}
      </header>

      <header className="sticky top-0 z-10 -mx-9 hidden items-end justify-between border-b border-halo-border bg-halo-bg px-9 pb-4 pt-6 md:flex">
        <div className="min-w-0">
          <h1 className="m-0 text-2xl font-semibold tracking-[-0.4px] text-halo-ink">{title}</h1>
          {subtitle != null && (
            <div className="mt-1 text-[13px] text-halo-ink-dim">{subtitle}</div>
          )}
        </div>
        {right != null && <div className="shrink-0 pl-4">{right}</div>}
      </header>
    </>
  )
}
