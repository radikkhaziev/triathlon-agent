interface EnduraiLogoProps {
  height?: number
  className?: string
}

export default function EnduraiLogo({ height = 48, className }: EnduraiLogoProps) {
  return (
    <svg
      viewBox="0 0 320 90"
      height={height}
      className={className}
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="EndurAI"
    >
      <polygon
        points="40,12 72,70 8,70"
        fill="none"
        stroke="var(--text-dim)"
        strokeWidth="2"
        strokeLinejoin="round"
        opacity="0.5"
      />
      <polyline
        points="6,48 20,48 27,48 32,24 38,64 44,38 49,52 56,48 74,48"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <text
        x="92"
        y="55"
        fontFamily="Inter, -apple-system, BlinkMacSystemFont, sans-serif"
        fontWeight="800"
        fontSize="38"
        letterSpacing="-1"
      >
        <tspan fill="var(--text)">Endur</tspan>
        <tspan fill="var(--green)">AI</tspan>
      </text>
    </svg>
  )
}
