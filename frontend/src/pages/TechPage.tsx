import { ArrowLeft, ExternalLink, FlaskConical } from 'lucide-react'
import { useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { Link } from 'react-router-dom'
import article from '../generated/technical-deep-dive/technical-deep-dive.md?raw'
import figureOne from '../generated/technical-deep-dive/figures/01-spurious-control.svg?url'
import figureTwo from '../generated/technical-deep-dive/figures/02-agent-system.svg?url'
import figureThree from '../generated/technical-deep-dive/figures/03-grpo-loop.svg?url'
import figureFour from '../generated/technical-deep-dive/figures/04-verifier-pipeline.svg?url'
import figureFive from '../generated/technical-deep-dive/figures/05-heldout-selection.svg?url'
import figureSix from '../generated/technical-deep-dive/figures/06-system-loop.svg?url'
import wordmark from '../generated/technical-deep-dive/verifierforge-wordmark.svg?url'

interface Chapter {
  title: string
  slug: string
  markdown: string
}

const figures: Record<string, string> = {
  './figures/01-spurious-control.svg': figureOne,
  './figures/02-agent-system.svg': figureTwo,
  './figures/03-grpo-loop.svg': figureThree,
  './figures/04-verifier-pipeline.svg': figureFour,
  './figures/05-heldout-selection.svg': figureFive,
  './figures/06-system-loop.svg': figureSix,
}

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

function splitArticle(markdown: string): { intro: string; chapters: Chapter[] } {
  const parts = markdown.split(/^## /gm)
  return {
    intro: parts[0].trim(),
    chapters: parts.slice(1).map((part) => {
      const [title, ...body] = part.split('\n')
      return { title: title.trim(), slug: slugify(title), markdown: body.join('\n').trim() }
    }),
  }
}

function Markdown({ children, eagerImages = false }: { children: string; eagerImages?: boolean }) {
  const markdownComponents: Components = {
    img: ({ src, alt }) => <img src={src ? figures[src] ?? src : undefined} alt={alt ?? ''} loading={eagerImages ? 'eager' : 'lazy'} />,
    a: ({ href = '', children: linkChildren }) => {
      const resolved = href.startsWith('#') || href.startsWith('http')
        ? href
        : new URL(href, 'https://github.com/xesws/verifierforge/blob/main/docs/blog/').href
      return <a href={resolved} target={resolved.startsWith('http') ? '_blank' : undefined} rel="noreferrer">
        {linkChildren}{resolved.startsWith('http') && <ExternalLink size={12} aria-hidden="true" />}
      </a>
    },
  }
  return (
    <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
      {children}
    </ReactMarkdown>
  )
}

export function TechPage() {
  const content = useMemo(() => splitArticle(article), [])
  const printMode = useMemo(() => new URLSearchParams(window.location.search).get('print') === '1', [])
  return (
    <div className={`tech-shell${printMode ? ' tech-print-export' : ''}`} data-chapter-count={content.chapters.length}>
      <header className="tech-topbar">
        <Link to="/discover" className="tech-brand" aria-label="VerifierForge product">
          <img src={wordmark} alt="VerifierForge" />
        </Link>
        <Link to="/discover" className="secondary-button tech-product-link"><ArrowLeft size={15} />Open the product</Link>
      </header>
      <main className="tech-layout" id="main-content">
        <aside className="tech-toc glass-panel" aria-label="Article contents">
          <span className="eyebrow"><FlaskConical size={14} />Technical deep dive</span>
          <strong>Evidence, not a pitch.</strong>
          <nav>
            {content.chapters.map((chapter, index) => (
              <a key={chapter.slug} href={`#${chapter.slug}`}><span>{String(index + 1).padStart(2, '0')}</span>{chapter.title}</a>
            ))}
          </nav>
          <a className="tech-source-link" href="https://github.com/xesws/verifierforge/blob/main/docs/blog/technical-deep-dive.md" target="_blank" rel="noreferrer">
            Read the versioned source <ExternalLink size={12} />
          </a>
        </aside>
        <article className="tech-article">
          <section className="tech-hero glass-panel">
            <span className="eyebrow">VerifierForge · v0.40.0 evidence edition</span>
            <Markdown eagerImages={printMode}>{content.intro}</Markdown>
            <div className="tech-proof-strip">
              <span><strong>58.3% → 78.3%</strong> held-out pass@1</span>
              <span><strong>8 checkpoints</strong> evaluated</span>
              <span><strong>6 figures</strong> generated from evidence</span>
            </div>
            <p className="tech-print-meta">12 chapters · 6 evidence figures · prepared 2026-07-21</p>
          </section>
          {content.chapters.map((chapter, index) => (
            <details className="tech-chapter glass-panel" id={chapter.slug} key={chapter.slug} open>
              <summary><span data-chapter-number={String(index + 1).padStart(2, '0')}>{chapter.title}</span><small>collapse / expand</small></summary>
              <div className="tech-markdown"><Markdown eagerImages={printMode}>{chapter.markdown}</Markdown></div>
            </details>
          ))}
        </article>
      </main>
    </div>
  )
}
