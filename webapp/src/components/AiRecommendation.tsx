import { renderMarkdown } from '../lib/markdown'

interface AiRecommendationProps {
  claude: string | null
}

export default function AiRecommendation({ claude }: AiRecommendationProps) {
  if (!claude) return null

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">🤖</span>
        <span className="text-[15px] font-bold">AI Рекомендация</span>
      </div>
      <div className="ai-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(claude) }} />
    </div>
  )
}
