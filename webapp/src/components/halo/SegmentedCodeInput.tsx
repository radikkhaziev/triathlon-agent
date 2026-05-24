import { useRef } from 'react'

/**
 * Segmented one-time-code entry (prototype `BLogin`). Controlled — exposes a
 * single joined string via `onChange` so the parent's verify-code handler
 * stays byte-identical (it still reads/sends one `code` string).
 */
export default function SegmentedCodeInput({
  value,
  onChange,
  length = 6,
  disabled = false,
  ariaLabel,
}: {
  value: string
  onChange: (v: string) => void
  length?: number
  disabled?: boolean
  ariaLabel?: string
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([])
  const chars = Array.from({ length }, (_, i) => value[i] ?? '')

  const setAt = (i: number, ch: string) => {
    const next = chars.slice()
    next[i] = ch
    onChange(next.join('').slice(0, length))
  }

  return (
    <div className="flex justify-between gap-2" role="group" aria-label={ariaLabel}>
      {chars.map((c, i) => (
        <input
          key={i}
          ref={el => {
            refs.current[i] = el
          }}
          value={c}
          disabled={disabled}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={1}
          onChange={e => {
            const ch = e.target.value.replace(/\D/g, '').slice(-1)
            setAt(i, ch)
            if (ch && i < length - 1) refs.current[i + 1]?.focus()
          }}
          onKeyDown={e => {
            if (e.key === 'Backspace' && !chars[i] && i > 0) refs.current[i - 1]?.focus()
          }}
          onPaste={e => {
            e.preventDefault()
            const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, length)
            if (digits) {
              onChange(digits)
              refs.current[Math.min(digits.length, length - 1)]?.focus()
            }
          }}
          className="h-12 w-0 flex-1 rounded-chip border border-halo-border bg-halo-surface text-center text-lg font-semibold text-halo-ink outline-none focus:border-halo-brand"
        />
      ))}
    </div>
  )
}
