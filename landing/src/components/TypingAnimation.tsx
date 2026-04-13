export default function TypingAnimation() {
  return (
    <span className="inline-flex items-center gap-1" role="img" aria-label="Typing">
      <Dot delay="0s" />
      <Dot delay="0.15s" />
      <Dot delay="0.3s" />
      <style>{`
        @keyframes typing-dot {
          0%, 60%, 100% { opacity: 0.25; transform: translateY(0); }
          30% { opacity: 1; transform: translateY(-2px); }
        }
      `}</style>
    </span>
  )
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-accent"
      style={{ animation: `typing-dot 1.2s ease-in-out ${delay} infinite` }}
    />
  )
}
