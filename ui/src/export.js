// Self-contained, dependency-free exporters for an agent result. The result is
// markdown text (the same string shown in the chat). We reuse md() for the rich
// formats (PDF/Word), emit raw text for txt/md, and an HTML table for Excel.
// Nothing here needs a backend or an npm package — it all runs in the browser.
import { md } from './md.js'

function ts() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

// A filesystem-safe file stem derived from the prompt/title plus a timestamp.
function safeBase(title) {
  const base =
    String(title || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'awcp-result'
  return `${base}-${ts()}`
}

function triggerDownload(filename, mime, content) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

const escapeHtml = (s) =>
  String(s).replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))

const DOC_STYLES = `
  body { font: 14px/1.6 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #111; margin: 32px; }
  h1 { font-size: 21px; margin: 0 0 4px; } h2 { font-size: 18px; } h3 { font-size: 15px; }
  code { background: #f2f2f2; padding: 1px 5px; border-radius: 4px; font-family: Consolas, monospace; font-size: 12.5px; }
  pre { background: #f6f6f6; border: 1px solid #ddd; border-radius: 6px; padding: 10px 12px; overflow: auto; }
  a { color: #0b66c3; }
  .meta { color: #777; font-size: 12px; margin: 0 0 20px; }
`

// A standalone HTML document. `forWord` adds the Office namespaces so Word opens
// it cleanly (no "may be corrupted" prompt) when saved with a .doc extension.
function htmlDoc(title, bodyHtml, forWord = false) {
  const htmlTag = forWord
    ? '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">'
    : '<html lang="en">'
  return `<!doctype html>${htmlTag}<head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>${DOC_STYLES}</style></head><body>
<h1>${escapeHtml(title)}</h1>
<div class="meta">Exported from AWCP · ${new Date().toLocaleString()}</div>
${bodyHtml}
</body></html>`
}

// Open the rendered result in a new window and trigger the print dialog, where
// the browser's "Save as PDF" produces the file. (No PDF library required.)
function printPdf(title, bodyHtml) {
  const w = window.open('', '_blank')
  if (!w) {
    alert('Allow pop-ups for this site to export as PDF — or pick another format.')
    return
  }
  w.document.write(htmlDoc(title, bodyHtml))
  w.document.close()
  w.focus()
  setTimeout(() => w.print(), 350) // let the new document lay out first
}

export const EXPORT_FORMATS = [
  { key: 'pdf', label: 'PDF', ext: '.pdf' },
  { key: 'doc', label: 'Word', ext: '.doc' },
  { key: 'xls', label: 'Excel', ext: '.xls' },
  { key: 'txt', label: 'Text', ext: '.txt' },
  { key: 'md', label: 'Markdown', ext: '.md' },
]

export function exportResult(format, result, title) {
  const base = safeBase(title)
  const text = String(result || '')
  const docTitle = (title && String(title).slice(0, 80)) || 'AWCP result'
  switch (format) {
    case 'txt':
      return triggerDownload(`${base}.txt`, 'text/plain;charset=utf-8', text)
    case 'md':
      return triggerDownload(`${base}.md`, 'text/markdown;charset=utf-8', text)
    case 'doc':
      // Word opens HTML saved with the msword MIME + a .doc extension.
      return triggerDownload(`${base}.doc`, 'application/msword', htmlDoc(docTitle, md(text), true))
    case 'xls': {
      // Excel opens an HTML table saved with the ms-excel MIME + a .xls
      // extension. One row per line so the text lands in cells, not one blob.
      const rows = text
        .split(/\r?\n/)
        .map((line) => `<tr><td>${escapeHtml(line)}</td></tr>`)
        .join('')
      const html = `<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body><table>${rows}</table></body></html>`
      return triggerDownload(`${base}.xls`, 'application/vnd.ms-excel', html)
    }
    case 'pdf':
    default:
      return printPdf(docTitle, md(text))
  }
}
