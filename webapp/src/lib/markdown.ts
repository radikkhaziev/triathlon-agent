export function renderMarkdown(text: string): string {
  if (!text) return ''

  // Escape HTML
  let html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  // Horizontal rules
  html = html.replace(/---/g, '<hr>')

  // Headers
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^##\s+(.+)$/gm, '<h3>$1</h3>')

  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')

  // Italic
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // Numbered sections
  html = html.replace(/^(\d+)\.\s+(.+)$/gm, '<strong>$1. $2</strong>')

  // Bullet lists
  html = html.replace(/^[-*]\s+(.+)$/gm, '<li>$1</li>')
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
  html = html.replace(/<\/ul>\s*<ul>/g, '')

  // Paragraphs
  html = html.split(/\n{2,}/).map(block => {
    block = block.trim()
    if (!block) return ''
    if (/^<(h[234]|ul|ol|li|hr)/.test(block)) return block
    return '<p>' + block.replace(/\n/g, '<br>') + '</p>'
  }).join('\n')

  return html
}
