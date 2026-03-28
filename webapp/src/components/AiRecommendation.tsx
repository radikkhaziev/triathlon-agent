import { useState } from 'react'
import TabSwitcher from './TabSwitcher'
import { renderMarkdown } from '../lib/markdown'

interface AiRecommendationProps {
  claude: string | null
  gemini: string | null
}

export default function AiRecommendation({ claude, gemini }: AiRecommendationProps) {
  const [activeTab, setActiveTab] = useState('claude')

  if (!claude) return null

  const hasTabs = !!gemini

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">🤖</span>
        <span className="text-[15px] font-bold">AI Рекомендация</span>
      </div>

      {hasTabs && (
        <TabSwitcher
          tabs={[
            { key: 'claude', label: 'Claude' },
            { key: 'gemini', label: 'Gemini' },
          ]}
          active={activeTab}
          onChange={setActiveTab}
        />
      )}

      {(activeTab === 'claude' || !hasTabs) && (
        <div className="ai-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(claude) }} />
      )}
      {activeTab === 'gemini' && gemini && (
        <div className="ai-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(gemini) }} />
      )}
    </div>
  )
}
