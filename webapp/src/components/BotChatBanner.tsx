import { useTranslation } from 'react-i18next'

interface BotChatBannerProps {
  botUsername: string | null
}

/**
 * Top banner shown when the authenticated user logged in via the
 * Telegram Login Widget but never opened a chat with the bot. Telegram's
 * sendMessage returns 400 chat-not-found in that state, so morning reports
 * and notifications silently no-op. The banner is the visible nudge — it
 * appears on every page (rendered in App.tsx above the Routes block) until
 * the user presses /start in the bot and reloads. See issue #266.
 *
 * **Sticky only on mobile** (`md:relative md:top-auto md:z-auto`). On desktop
 * (Halo-v3) the per-page `TopBar` is itself `sticky top-0`; two competing
 * stickies on the same viewport collided (banner z-40 was painting on top
 * of the TopBar title). Desktop: banner is a normal first-row block that
 * scrolls away; the sidebar carries persistent navigation so the loss of
 * the "always-visible" property is acceptable. Mobile keeps the sticky
 * behaviour — no per-page sticky competes there (mobile TopBar is `md:hidden`).
 */
export default function BotChatBanner({ botUsername }: BotChatBannerProps) {
  const { t } = useTranslation()
  const href = botUsername ? `https://t.me/${botUsername}?start=fromwidget` : null

  return (
    <div className="sticky top-0 z-40 md:relative md:top-auto md:z-auto bg-amber-500/15 border-b border-amber-500/30 px-4 py-2 text-[12px] text-text">
      <div className="max-w-[800px] mx-auto flex items-center justify-between gap-3">
        <span className="leading-snug">{t('bot_chat_banner.message')}</span>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 px-3 py-1 rounded-lg bg-accent text-white text-[12px] font-semibold no-underline"
          >
            {t('bot_chat_banner.cta')}
          </a>
        ) : null}
      </div>
    </div>
  )
}
