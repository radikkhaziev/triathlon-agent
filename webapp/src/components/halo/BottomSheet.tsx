import { useEffect, type ReactNode } from 'react'

/**
 * Halo bottom-sheet modal — slides up from the bottom of the viewport with a
 * dimmed backdrop. iOS-style picker pattern, mobile-first; on desktop the
 * sheet is centered + capped at 480px so it doesn't span the whole screen.
 *
 * Used by `<SportTypeSelect>` in Settings (replaces the inline dropdown by
 * explicit design request 2026-05-23: «Settings этот select не по дизайну»).
 * Keep the API small — `open` + `onClose` + `title?` + children. Caller owns
 * the option list / form / whatever lives inside.
 *
 * A11y: Escape closes; backdrop click closes; `role="dialog"` +
 * `aria-modal="true"`; body scroll locked while open (prevents the underlying
 * page from scrolling behind the sheet, especially on touch).
 *
 * NOT a portal — rendered in-place. Parent must own the conditional render
 * (`{open && <BottomSheet …/>}` would also work, but here the component
 * itself returns null when closed so callers don't need the guard).
 */
interface BottomSheetProps {
  open: boolean
  onClose: () => void
  /** Optional centered title at the top of the sheet. */
  title?: string
  children: ReactNode
}

export default function BottomSheet({ open, onClose, title, children }: BottomSheetProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // Lock body scroll while the sheet is up. Without this, on iOS Safari the
    // background page scrolls under the sheet when the user touch-drags.
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-end justify-center"
    >
      {/* Backdrop — tap to close. */}
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 cursor-default border-none bg-halo-ink/60 p-0"
      />
      {/* Sheet panel. `max-w` mirrors the Layout primitive's mobile width so
          the sheet feels consistent on desktop (centered, not full-bleed). */}
      <div className="relative w-full max-w-[480px] rounded-t-[20px] bg-halo-surface px-4 pb-6 pt-3 shadow-card">
        {/* Drag-handle indicator — purely visual cue that this is a sheet,
            no actual drag behaviour. */}
        <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-halo-ink-dimmer/60" />
        {title && (
          <div className="mb-2 px-2 text-center text-[15px] font-semibold tracking-[-0.2px] text-halo-ink">
            {title}
          </div>
        )}
        {children}
      </div>
    </div>
  )
}
