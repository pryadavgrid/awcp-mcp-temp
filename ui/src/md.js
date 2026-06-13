// Minimal, dependency-free markdown -> HTML for agent results (bold, code,
// headings, links, bullets). Input is escaped first, so this is safe to inject.
export function md(t) {
  if (!t) return ''
  t = String(t).replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))
  t = t.replace(/```([\s\S]*?)```/g, (m, c) => '<pre class="cb">' + c.replace(/^\n/, '') + '</pre>')
  t = t.replace(/`([^`\n]+)`/g, '<code>$1</code>')
  t = t
    .replace(/^#{4}\s?(.*)$/gm, '<h4>$1</h4>')
    .replace(/^#{3}\s?(.*)$/gm, '<h3>$1</h3>')
    .replace(/^#{1,2}\s?(.*)$/gm, '<h2>$1</h2>')
  t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  t = t.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>')
  t = t.replace(/^\s*[-*]\s+(.*)$/gm, '&bull; $1')
  t = t.replace(/\n{2,}/g, '<br><br>').replace(/\n/g, '<br>')
  return t
}
