import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

// Strip YAML frontmatter, then render markdown → HTML (for digest + page bodies).
export function renderMarkdown(src: string): string {
  const body = src.replace(/^---\n[\s\S]*?\n---\n?/, '').trim()
  return marked.parse(body) as string
}
