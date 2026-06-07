// ── Gare d'Appalto (R.A.M.) ──────────────────────────────────────────────────
// A section separate from the sales chatbot. On open it scrapes the R.A.M. Logistica
// Infrastrutture e Trasporti SpA procurement portal (bandi in corso + in aggiudicazione),
// shows a live loading spinner as each bando is indexed, highlights each bando's
// participation requirements, and exposes a chatbot scoped to the scraped gare.

type Tender = {
  id: string
  title: string
  cig: string
  tipologia: string
  stato: string
  category: string
  data_pubblicazione: string
  data_scadenza: string
  importo: string | number | null
  detail_url: string
  documents?: { label: string; url: string; chunks: number }[]
  requirements?: string[]
  chunks?: number
}
type Category = { key: string; label: string; tenders: Tender[] }
type ChatMsg = { role: 'user' | 'assistant'; content: string }

type BandiQueryResponse = {
  response: string
  sources: string[]
  grounded: boolean
  ambiguous: boolean
  top_score: number
}

const I = {
  gavel:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5"><path d="M14 4l6 6"/><path d="M9.5 8.5l6 6"/><path d="M3 21h8"/><path d="M5.5 12.5l6 6"/><path d="m8 9 4-4 7 7-4 4z"/></svg>',
  refresh:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3 8"/><path d="M3 3.5V8h4.5"/></svg>',
  doc:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
  check:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
  send:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M5 12h13M12 6l6 6-6 6"/></svg>',
  link:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3 w-3"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>',
  spark:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>',
}

const esc = (s: string): string => {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}
function elFrom(html: string): HTMLElement {
  const t = document.createElement('template')
  t.innerHTML = html.trim()
  return t.content.firstChild as HTMLElement
}

const STATO_TONE: Record<string, string> = {
  in_corso: 'bando-badge-green',
  in_svolgimento: 'bando-badge-green',
  scaduta: 'bando-badge-amber',
  aggiudicata: 'bando-badge-azure',
  conclusa_affidata: 'bando-badge-azure',
  conclusa: 'bando-badge-slate',
  chiusa: 'bando-badge-slate',
  annullata: 'bando-badge-slate',
}

function fmtImporto(v: string | number | null): string {
  if (v === null || v === undefined || v === '') return ''
  const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[^\d.,]/g, '').replace(',', '.'))
  if (isNaN(n) || n === 0) return ''
  return '€ ' + n.toLocaleString('it-IT', { maximumFractionDigits: 0 })
}

// Module-level state so a re-open does not lose the scraped data.
let initialized = false
const state = {
  tenders: new Map<string, Tender>(),
  scraping: false,
  history: [] as ChatMsg[],
}

const $b = <T extends HTMLElement>(s: string) => document.querySelector<T>(s) as T

export function initBandi(container: HTMLElement) {
  if (initialized) return
  initialized = true

  container.innerHTML = `
    <!-- header -->
    <div class="rail-texture relative flex items-center gap-4 bg-navy-900 px-7 py-3 text-white">
      <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400/60 to-transparent"></div>
      <div class="grid h-9 w-9 place-items-center rounded-xl bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${I.gavel}</div>
      <div>
        <div class="font-display text-lg leading-none">Gare d'Appalto</div>
        <div class="mt-1 text-[10px] uppercase tracking-[0.18em] text-navy-300">R.A.M. Logistica Infrastrutture e Trasporti S.p.A.</div>
      </div>
      <div class="ml-auto flex items-center gap-3">
        <span id="bandi-status" class="hidden text-[11px] text-navy-300 sm:block"></span>
        <button id="bandi-refresh" class="group flex items-center gap-2 rounded-xl bg-white/[0.06] px-3.5 py-2 text-sm font-medium text-white ring-1 ring-white/10 transition hover:bg-white/[0.1] disabled:opacity-40">
          ${I.refresh}<span>Aggiorna scraping</span>
        </button>
      </div>
    </div>

    <!-- body: progress + cards -->
    <div id="bandi-body" class="flex-1 overflow-y-auto px-6 py-6">
      <div id="bandi-progress" class="mx-auto hidden max-w-3xl"></div>
      <div id="bandi-empty" class="mx-auto max-w-md py-24 text-center">
        <div class="pulse-ring mx-auto mb-7 inline-grid h-14 w-14 place-items-center rounded-2xl bg-navy-850 text-azure-400 ring-1 ring-azure-400/20">${I.gavel}</div>
        <h2 class="font-display text-2xl text-ink">Scraping dei bandi R.A.M.</h2>
        <p class="mt-3 text-[13px] leading-relaxed text-slatev">
          Avvia lo scraping del portale acquisti per recuperare i bandi
          <span class="font-semibold text-ink">in corso</span> e
          <span class="font-semibold text-ink">in aggiudicazione</span>,
          indicizzarli ed evidenziarne i requisiti di partecipazione.
        </p>
        <button id="bandi-start" class="mt-7 inline-flex items-center gap-2 rounded-xl bg-azure-500 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-azure-500/20 transition hover:bg-azure-600 active:scale-95">
          ${I.gavel}<span>Avvia scraping</span>
        </button>
      </div>
      <div id="bandi-grid" class="mx-auto max-w-5xl space-y-8"></div>
    </div>

    <!-- chatbot dock -->
    <div class="border-t border-haze bg-card/80 px-6 py-4 backdrop-blur-md">
      <div id="bandi-messages" class="mx-auto mb-3 hidden max-h-[38vh] max-w-3xl space-y-3 overflow-y-auto"></div>
      <form id="bandi-form" class="mx-auto flex max-w-3xl items-center gap-2.5 rounded-2xl border border-haze bg-card px-3 py-2 shadow-lg shadow-navy-950/20 transition-all focus-within:border-azure-400/50 focus-within:ring-3 focus-within:ring-azure-500/10">
        <span class="pl-1 text-azure-400/60">${I.spark}</span>
        <input id="bandi-input" autocomplete="off" placeholder="Chiedi sui bandi: requisiti, scadenze, importi, CIG…"
          class="flex-1 bg-transparent py-2 text-[13.5px] placeholder:text-slatev/50 focus:outline-none" />
        <button id="bandi-send" class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-azure-500 text-white shadow-sm transition hover:bg-azure-600 active:scale-95 disabled:opacity-35">${I.send}</button>
      </form>
      <p class="mx-auto mt-2 max-w-3xl text-center font-mono text-[9.5px] uppercase tracking-[0.14em] text-slatev/60">Risponde solo sui documenti di gara indicizzati · cita le fonti</p>
    </div>`

  $b('#bandi-start').addEventListener('click', startScrape)
  $b('#bandi-refresh').addEventListener('click', startScrape)
  $b('#bandi-form').addEventListener('submit', (e) => {
    e.preventDefault()
    const q = $b<HTMLInputElement>('#bandi-input').value.trim()
    if (q) askBandi(q)
  })

  // Load any previously scraped data (cache survives across page reloads on the server).
  void loadCache()
}

async function loadCache() {
  try {
    const data = await (await fetch('/api/bandi')).json()
    const cats: Category[] = data.categories || []
    const total = cats.reduce((n, c) => n + c.tenders.length, 0)
    if (total > 0) {
      state.tenders.clear()
      for (const c of cats) for (const t of c.tenders) state.tenders.set(t.id, t)
      renderGrid()
      setStatus(`${total} bandi indicizzati`)
    }
  } catch {
    /* backend offline — keep the empty-state call to action */
  }
}

function setStatus(text: string) {
  const s = $b('#bandi-status')
  s.textContent = text
  s.classList.toggle('hidden', !text)
}

// ── scraping (Server-Sent Events) ─────────────────────────────────────────────
function startScrape() {
  if (state.scraping) return
  state.scraping = true
  state.tenders.clear()
  $b('#bandi-empty').classList.add('hidden')
  $b('#bandi-grid').innerHTML = ''
  $b<HTMLButtonElement>('#bandi-refresh').disabled = true
  $b<HTMLButtonElement>('#bandi-start').disabled = true

  const prog = $b('#bandi-progress')
  prog.classList.remove('hidden')
  prog.innerHTML = `
    <div class="flex items-center gap-4 rounded-2xl border border-haze bg-card/80 px-5 py-4 backdrop-blur-sm">
      <span class="bandi-spinner"></span>
      <div class="min-w-0 flex-1">
        <div id="bandi-prog-label" class="text-[13px] font-medium text-ink">Connessione al portale R.A.M.…</div>
        <div class="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-haze">
          <div id="bandi-prog-bar" class="h-full w-0 rounded-full bg-azure-500 transition-all duration-300"></div>
        </div>
      </div>
      <span id="bandi-prog-count" class="shrink-0 font-mono text-[11px] tabular-nums text-slatev">0 / 0</span>
    </div>`

  const es = new EventSource('/api/bandi/scrape')
  let total = 0

  es.onmessage = (ev) => {
    let m: any
    try { m = JSON.parse(ev.data) } catch { return }

    if (m.phase === 'listing') {
      total = m.total
      $b('#bandi-prog-label').textContent =
        total > 0 ? `Trovati ${total} bandi — download e indicizzazione…` : 'Nessun bando trovato.'
      $b('#bandi-prog-count').textContent = `0 / ${total}`
    } else if (m.phase === 'tender') {
      const t: Tender = { ...m.tender, documents: m.documents, requirements: m.requirements, chunks: m.chunks }
      state.tenders.set(t.id, t)
      renderGrid()
      const pct = total ? Math.round((m.index / total) * 100) : 0
      $b<HTMLElement>('#bandi-prog-bar').style.width = pct + '%'
      $b('#bandi-prog-count').textContent = `${m.index} / ${total}`
      $b('#bandi-prog-label').textContent = `Indicizzato: ${t.title.slice(0, 70)}${t.title.length > 70 ? '…' : ''}`
    } else if (m.phase === 'done') {
      es.close()
      finishScrape()
      const parts: [string, number][] = m.by_category ? Object.entries(m.by_category) : []
      const summary = parts.map(([k, n]) => `${n} ${k === 'in_corso' ? 'in corso' : 'in aggiudicazione'}`).join(' · ')
      setStatus(`${m.total} bandi · ${m.chunks} chunk indicizzati`)
      $b('#bandi-prog-label').textContent = `Completato — ${summary || m.total + ' bandi'}.`
      $b<HTMLElement>('#bandi-prog-bar').style.width = '100%'
      setTimeout(() => $b('#bandi-progress').classList.add('hidden'), 2200)
    } else if (m.phase === 'error') {
      es.close()
      finishScrape()
      $b('#bandi-progress').innerHTML =
        `<div class="rounded-xl border border-red-400/25 bg-red-400/10 px-4 py-3 font-mono text-[12px] text-red-400">⚠ Scraping fallito: ${esc(m.message || 'errore sconosciuto')}</div>`
    }
  }

  es.onerror = () => {
    if (!state.scraping) return // already finished/closed
    es.close()
    finishScrape()
    $b('#bandi-prog-label')?.replaceChildren(
      document.createTextNode('Connessione interrotta. Verifica che il backend sia attivo e riprova.')
    )
  }
}

function finishScrape() {
  state.scraping = false
  $b<HTMLButtonElement>('#bandi-refresh').disabled = false
  const start = document.querySelector<HTMLButtonElement>('#bandi-start')
  if (start) start.disabled = false
  if (state.tenders.size === 0) $b('#bandi-empty').classList.remove('hidden')
}

// ── cards ─────────────────────────────────────────────────────────────────────
const CAT_META: Record<string, { label: string; accent: string }> = {
  in_corso: { label: 'Bandi in corso', accent: 'text-signal-green' },
  aggiudicazione: { label: 'Bandi in aggiudicazione', accent: 'text-azure-400' },
}

function renderGrid() {
  const grid = $b('#bandi-grid')
  const byCat: Record<string, Tender[]> = { in_corso: [], aggiudicazione: [] }
  for (const t of state.tenders.values()) (byCat[t.category] ||= []).push(t)

  grid.innerHTML = ''
  for (const key of ['in_corso', 'aggiudicazione']) {
    const list = byCat[key] || []
    if (!list.length) continue
    const meta = CAT_META[key]
    const section = elFrom(`
      <section>
        <div class="mb-3 flex items-center gap-2.5">
          <span class="h-1.5 w-1.5 rounded-full bg-current ${meta.accent}"></span>
          <h3 class="font-display text-lg text-ink">${meta.label}</h3>
          <span class="rounded-full bg-haze px-2 py-0.5 font-mono text-[10px] text-slatev">${list.length}</span>
        </div>
        <div class="grid gap-3.5 md:grid-cols-2"></div>
      </section>`)
    const cardsWrap = section.querySelector('div.grid') as HTMLElement
    for (const t of list) cardsWrap.appendChild(card(t))
    grid.appendChild(section)
  }
}

function card(t: Tender): HTMLElement {
  const tone = STATO_TONE[t.stato] || 'bando-badge-slate'
  const importo = fmtImporto(t.importo)
  const reqs = t.requirements || []
  const docs = t.documents || []

  const meta: string[] = []
  if (t.cig && t.cig !== 'M-00000001') meta.push(`CIG ${esc(t.cig)}`)
  if (t.tipologia) meta.push(esc(t.tipologia))
  if (t.data_scadenza) meta.push(`Scad. ${esc(t.data_scadenza)}`)
  if (importo) meta.push(importo)

  const reqBlock = reqs.length
    ? `<details class="group mt-3">
         <summary class="flex cursor-pointer list-none items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.15em] text-azure-400/80 select-none hover:text-azure-400">
           ${I.check}<span>Requisiti evidenziati (${reqs.length})</span>
           <span class="ml-0.5 transition-transform group-open:rotate-90">›</span>
         </summary>
         <ul class="mt-2.5 space-y-1.5">
           ${reqs.map((r) => `<li class="flex gap-2 text-[12px] leading-snug text-ink/85"><span class="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-azure-400"></span><span>${esc(r)}</span></li>`).join('')}
         </ul>
       </details>`
    : `<div class="mt-3 text-[11px] italic text-slatev/70">Requisiti non estratti automaticamente — consulta il disciplinare.</div>`

  const docBlock = docs.length
    ? `<div class="mt-3 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.15em] text-slatev">${I.doc}<span>${docs.length} documenti · ${t.chunks || 0} chunk</span></div>`
    : ''

  const linkBlock = t.detail_url
    ? `<a href="${esc(t.detail_url)}" target="_blank" rel="noopener" class="mt-3 inline-flex items-center gap-1.5 text-[11px] font-medium text-azure-400 hover:text-azure-300">${I.link}<span>Apri sul portale</span></a>`
    : ''

  return elFrom(`
    <article class="bando-card rounded-2xl border border-haze bg-card/90 p-4 shadow-lg shadow-navy-950/10 backdrop-blur-sm transition hover:border-azure-400/30">
      <div class="mb-2 flex items-start gap-2">
        <span class="bando-badge ${tone}">${esc((t.stato || '').replace(/_/g, ' ') || 'n/d')}</span>
      </div>
      <h4 class="text-[13.5px] font-semibold leading-snug text-ink">${esc(t.title)}</h4>
      ${meta.length ? `<div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slatev">${meta.map((m) => `<span>${m}</span>`).join('<span class="text-haze">·</span>')}</div>` : ''}
      ${reqBlock}
      ${docBlock}
      ${linkBlock}
    </article>`)
}

// ── chatbot ────────────────────────────────────────────────────────────────────
function addBandiMsg(node: HTMLElement) {
  const box = $b('#bandi-messages')
  box.classList.remove('hidden')
  box.appendChild(node)
  box.scrollTop = box.scrollHeight
}

async function askBandi(question: string) {
  $b<HTMLInputElement>('#bandi-input').value = ''
  addBandiMsg(elFrom(`
    <div class="flex justify-end">
      <div class="max-w-[80%] rounded-2xl rounded-tr-sm bg-navy-700 px-4 py-2.5 text-[13px] leading-relaxed text-white ring-1 ring-azure-400/10">${esc(question)}</div>
    </div>`))

  const thinking = elFrom(`
    <div class="flex">
      <div class="flex items-center gap-3 rounded-2xl rounded-tl-sm border border-haze bg-card/80 px-4 py-2.5 text-[12px] text-slatev">
        <span class="bandi-spinner bandi-spinner-sm"></span><span class="font-mono">Ricerca nei bandi…</span>
      </div>
    </div>`)
  addBandiMsg(thinking)

  const send = $b<HTMLButtonElement>('#bandi-send')
  send.disabled = true
  try {
    const res = await fetch('/api/bandi/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history: state.history }),
    })
    thinking.remove()
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      addBandiMsg(errBubble(err.detail || `Errore ${res.status}`))
      return
    }
    const data: BandiQueryResponse = await res.json()
    addBandiMsg(answerBubble(data))
    state.history.push({ role: 'user', content: question })
    state.history.push({ role: 'assistant', content: data.response })
  } catch (e) {
    thinking.remove()
    addBandiMsg(errBubble(String(e)))
  } finally {
    send.disabled = false
    $b<HTMLInputElement>('#bandi-input').focus()
  }
}

function answerBubble(r: BandiQueryResponse): HTMLElement {
  const tone = r.ambiguous
    ? 'border-l-[var(--color-signal-amber)]'
    : r.grounded
      ? 'border-l-[var(--color-signal-green)]'
      : 'border-l-[var(--color-signal-slate)]'
  const sources = r.sources?.length
    ? `<div class="mt-3 border-t border-haze pt-2.5">
         <div class="mb-1.5 flex items-center gap-1.5 text-[9px] font-mono uppercase tracking-[0.18em] text-slatev">${I.doc}<span>Fonti</span></div>
         ${r.sources.map((s, i) => `<div class="text-[11px] text-slatev"><span class="font-mono text-azure-400">[${i + 1}]</span> ${esc(s)}</div>`).join('')}
       </div>`
    : ''
  return elFrom(`
    <div class="flex">
      <div class="w-full border-l-[3px] ${tone} rounded-xl border border-haze bg-card/90 px-4 py-3 shadow-sm">
        <div class="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">${esc(r.response)}</div>
        ${sources}
      </div>
    </div>`)
}

function errBubble(msg: string): HTMLElement {
  return elFrom(`
    <div class="flex">
      <div class="w-full rounded-xl border border-red-400/25 bg-red-400/10 px-4 py-2.5 font-mono text-[12px] text-red-400">⚠ ${esc(msg)}</div>
    </div>`)
}
