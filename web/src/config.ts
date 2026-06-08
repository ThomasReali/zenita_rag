// ── Configura Offerta (bozza d'offerta grounded) ─────────────────────────────
// A section separate from the chat: the operator describes a customer scenario and the
// assistant drafts a NON-BINDING offer configuration grounded in the company KB, with
// inline citations and the relevant normative constraints. Calls POST /api/configure.

type ConfigureResponse = {
  scenario: string
  draft: string
  sources: string[]
  source_links?: (string | null)[]
  grounded: boolean
  top_score: number
  latency_ms?: number
}

const I = {
  wand:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5"><path d="M15 4V2M15 10V8M11 6H9M21 6h-2M18 9l-1.5-1.5M18 3l-1.5 1.5M4 20l9-9M13.5 6.5 17 10"/></svg>',
  doc:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
  send:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M5 12h13M12 6l6 6-6 6"/></svg>',
}

const esc = (s: string): string => {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}

// Minimal markdown for the draft: escape, **bold**, bullet lists, line breaks.
function renderRich(src: string): string {
  const lines = esc(src).split('\n')
  let html = ''
  let inList = false
  for (const raw of lines) {
    let line = raw.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    const li = line.match(/^\s*[-*•]\s+(.*)$/)
    if (li) {
      if (!inList) { html += '<ul class="my-1.5 ml-4 list-disc space-y-1">'; inList = true }
      html += `<li>${li[1]}</li>`
      continue
    }
    if (inList) { html += '</ul>'; inList = false }
    html += line.trim() ? `<p class="my-1.5">${line}</p>` : ''
  }
  if (inList) html += '</ul>'
  return html
}

let initialized = false
let root: HTMLElement
const $c = <T extends HTMLElement>(s: string) => root.querySelector<T>(s)!

const EXAMPLES = [
  'Comune medio (40.000 abitanti) che vuole istituire una ZTL nel centro storico e controllare la velocità su due arterie urbane.',
  'Strada extraurbana con incidenti frequenti: serve controllo velocità omologato e rilevazione del passaggio con rosso a un incrocio.',
  'Comune piccolo con budget limitato: priorità a una soluzione di controllo accessi ZTL semplice da installare.',
]

export function initConfig(container: HTMLElement) {
  if (initialized) return
  initialized = true
  root = container

  container.innerHTML = `
    <!-- header -->
    <div class="rail-texture relative flex items-center gap-4 bg-navy-900 px-7 py-3 text-white">
      <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400/60 to-transparent"></div>
      <div class="grid h-9 w-9 place-items-center rounded-xl bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${I.wand}</div>
      <div>
        <div class="font-display text-lg leading-none">Configura Offerta</div>
        <div class="mt-1 text-[10px] uppercase tracking-[0.18em] text-navy-300">Bozza grounded · non vincolante</div>
      </div>
    </div>

    <!-- body -->
    <div class="flex-1 overflow-y-auto px-6 py-6">
      <div class="mx-auto max-w-3xl">
        <form id="cfg-form" class="rounded-2xl border border-haze bg-card/80 p-5 shadow-lg shadow-navy-950/10 backdrop-blur-sm">
          <label class="mb-2 block text-[11px] font-semibold uppercase tracking-[0.16em] text-slatev">Scenario cliente</label>
          <textarea id="cfg-scenario" rows="3" placeholder="Descrivi il cliente e le sue esigenze (es. Comune medio, ZTL + controllo velocità)…"
            class="w-full resize-y rounded-xl border border-haze bg-card px-3.5 py-3 text-[13.5px] leading-relaxed text-ink placeholder:text-slatev/50 focus:border-azure-400/50 focus:outline-none focus:ring-3 focus:ring-azure-500/10"></textarea>

          <label class="mb-2 mt-4 block text-[11px] font-semibold uppercase tracking-[0.16em] text-slatev">Esigenze specifiche <span class="font-normal normal-case text-slatev/60">(opzionale, separate da virgola)</span></label>
          <input id="cfg-needs" autocomplete="off" placeholder="es. ZTL varchi, controllo velocità, semaforo rosso"
            class="w-full rounded-xl border border-haze bg-card px-3.5 py-2.5 text-[13px] text-ink placeholder:text-slatev/50 focus:border-azure-400/50 focus:outline-none focus:ring-3 focus:ring-azure-500/10" />

          <div class="mt-3 flex flex-wrap gap-1.5" id="cfg-examples"></div>

          <div class="mt-4 flex items-center justify-between">
            <p class="text-[11px] text-slatev/70">Solo dai documenti aziendali · cita le fonti · niente prezzi inventati</p>
            <button id="cfg-go" type="submit" class="inline-flex items-center gap-2 rounded-xl bg-azure-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-azure-500/20 transition hover:bg-azure-600 active:scale-95 disabled:opacity-40">
              ${I.wand}<span>Genera bozza</span>
            </button>
          </div>
        </form>

        <div id="cfg-result" class="mt-6"></div>
      </div>
    </div>`

  const chips = $c('#cfg-examples')
  EXAMPLES.forEach((ex) => {
    const b = document.createElement('button')
    b.type = 'button'
    b.className = 'rounded-lg border border-haze bg-haze/30 px-2.5 py-1 text-left text-[11px] text-slatev transition hover:bg-haze/60 hover:text-ink'
    b.textContent = ex.length > 54 ? ex.slice(0, 54) + '…' : ex
    b.addEventListener('click', () => { $c<HTMLTextAreaElement>('#cfg-scenario').value = ex })
    chips.appendChild(b)
  })

  $c('#cfg-form').addEventListener('submit', onSubmit)
}

async function onSubmit(e: Event) {
  e.preventDefault()
  const scenario = $c<HTMLTextAreaElement>('#cfg-scenario').value.trim()
  if (!scenario) return
  const needs = $c<HTMLInputElement>('#cfg-needs').value.split(',').map((s) => s.trim()).filter(Boolean)

  const go = $c<HTMLButtonElement>('#cfg-go')
  const result = $c('#cfg-result')
  go.disabled = true
  result.innerHTML = `
    <div class="flex items-center gap-3 rounded-2xl border border-haze bg-card/80 px-5 py-4 text-[12px] text-slatev">
      <span class="relative flex h-2 w-2"><span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-azure-400/60"></span><span class="relative inline-flex h-2 w-2 rounded-full bg-azure-400"></span></span>
      <span class="font-mono tracking-wide">Compongo la bozza dai documenti…</span>
    </div>`

  try {
    const res = await fetch('/api/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario, needs }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      result.innerHTML = errorCard(err.detail || `Errore ${res.status}`)
      return
    }
    renderDraft(await res.json())
  } catch (err) {
    result.innerHTML = errorCard(String(err))
  } finally {
    go.disabled = false
  }
}

function errorCard(msg: string): string {
  return `<div class="rounded-xl border border-red-400/25 bg-red-400/10 px-4 py-3 font-mono text-[12px] text-red-400">⚠ ${esc(msg)}</div>`
}

function renderDraft(r: ConfigureResponse) {
  const result = $c('#cfg-result')
  const badge = r.grounded
    ? '<span class="status-lozenge lozenge-green"><span class="lozenge-dot"></span>Bozza grounded</span>'
    : '<span class="status-lozenge lozenge-slate"><span class="lozenge-dot"></span>Materiale insufficiente</span>'

  const sources = r.sources.length
    ? `<div class="mt-4 border-t border-haze pt-3.5">
         <div class="mb-2 flex items-center gap-2 text-[9.5px] font-mono uppercase tracking-[0.18em] text-slatev">${I.doc}<span>Fonti citate</span></div>
         ${r.sources.map((s, i) => {
           const url = r.source_links?.[i]
           const txt = url
             ? `<a href="${esc(url)}" target="_blank" rel="noopener" class="source-ref-text underline decoration-dotted underline-offset-2 transition-colors hover:text-azure-400" title="Apri la fonte ufficiale su mit.gov.it">${esc(s)} ↗</a>`
             : `<span class="source-ref-text">${esc(s)}</span>`
           return `<div class="source-ref text-slatev"><span class="source-ref-num">[${i + 1}]</span>${txt}</div>`
         }).join('')}
       </div>`
    : ''

  result.innerHTML = `
    <div class="resp-card border-l-[3px] ${r.grounded ? 'border-l-[var(--color-signal-green)]' : 'border-l-[var(--color-signal-slate)]'} border border-haze bg-card/90 px-5 py-4 shadow-lg shadow-navy-950/20 backdrop-blur-sm">
      <div class="mb-3.5 flex items-center gap-3">
        ${badge}
        <span class="ml-auto font-mono text-[10px] text-slatev tabular-nums">${r.top_score.toFixed(2)}</span>
      </div>
      <div class="text-[13.5px] leading-relaxed text-ink">${renderRich(r.draft)}</div>
      ${sources}
    </div>`
}
