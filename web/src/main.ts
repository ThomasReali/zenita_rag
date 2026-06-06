import './style.css'

interface QueryResponse {
  query: string
  standalone_query: string
  response: string
  sources: string[]
  context: string[]
  model: string
  grounded: boolean
  ambiguous: boolean
  top_score: number
}
type ChatMessage = { role: 'user' | 'assistant'; content: string }

const history: ChatMessage[] = []

const SUGGESTIONS = [
  'Quali requisiti per l’omologazione degli autovelox?',
  'Come funziona la gestione di una ZTL?',
  'Differenza tra approvazione e omologazione di un rilevatore?',
  'Cosa prevede il Codice della Strada sui limiti di velocità?',
]

const ROLE_HINTS: Record<string, string> = {
  sales: 'Linguaggio cliente · benefici · risposte brevi',
  presales: 'Tecnico · parametri e specifiche · fonti inline',
  bid_manager: 'Conformità · riferimenti normativi · adempimenti',
}
let currentRole = localStorage.getItem('np_role') || 'presales'

// Opaque identifiers for the GDPR query log. They carry no PII themselves and are
// NULLed server-side by the nightly anonymization job once past retention.
function stableId(store: Storage, key: string): string {
  let v = store.getItem(key)
  if (!v) {
    v = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`)
    store.setItem(key, v)
  }
  return v
}
const userId = stableId(localStorage, 'np_user')      // stable across sessions
const sessionId = stableId(sessionStorage, 'np_session')  // per browser session

const ICONS = {
  pulse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5"><path d="M3 12h4l2-6 4 12 2.5-6H21"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M12 3l7 3v5c0 4.6-3.1 7.8-7 9-3.9-1.2-7-4.4-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>',
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M5 12h13M12 6l6 6-6 6"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
  reset: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4 transition group-hover:-rotate-180 duration-500"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3 8"/><path d="M3 3.5V8h4.5"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><circle cx="12" cy="8" r="3.4"/><path d="M5.5 20c.6-3.3 3.2-5 6.5-5s5.9 1.7 6.5 5"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
}

const $ = <T extends HTMLElement>(s: string) => document.querySelector<T>(s)!
const messages = () => $('#messages')
const scrollDown = () => { messages().scrollTop = messages().scrollHeight }

function el(html: string): HTMLElement {
  const t = document.createElement('template')
  t.innerHTML = html.trim()
  return t.content.firstChild as HTMLElement
}
function esc(s: string): string {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}
const tile = (label: string, val: string | number) =>
  `<div class="rounded-xl bg-white/5 px-3 py-2.5 ring-1 ring-white/10">
     <div class="font-mono text-xl font-medium text-white">${val}</div>
     <div class="mt-0.5 text-[10px] uppercase tracking-wider text-navy-300">${label}</div>
   </div>`

function render() {
  $('#app').innerHTML = `
  <div class="flex h-screen overflow-hidden text-ink">

    <!-- COMMAND RAIL -->
    <aside class="rail-texture relative w-80 shrink-0 bg-navy-900 text-white flex flex-col">
      <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400 to-transparent"></div>

      <div class="reveal px-7 pt-7 pb-5" style="animation-delay:.02s">
        <div class="flex items-center gap-3">
          <div class="grid h-10 w-10 place-items-center rounded-xl bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${ICONS.pulse}</div>
          <div>
            <div class="font-display text-[26px] leading-none tracking-tight">NextPulse</div>
            <div class="mt-1.5 text-[10px] uppercase tracking-[0.2em] text-navy-300">Sales Assistant · Engine SpA</div>
          </div>
        </div>
      </div>
      <div class="mx-7 h-px bg-white/10"></div>

      <div class="reveal px-7 py-5" style="animation-delay:.09s">
        <div class="mb-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-navy-300">Knowledge base</div>
        <div id="kb" class="grid grid-cols-2 gap-2.5">${tile('Documenti', '—')}${tile('Chunk', '—')}</div>
        <div id="model" class="mt-3 flex items-center gap-2 rounded-lg bg-white/5 px-3 py-2 text-[11px] text-navy-300 ring-1 ring-white/10">
          <span class="relative flex h-1.5 w-1.5"><span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/70"></span><span class="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400"></span></span>
          <span class="model-name truncate font-mono">connessione…</span>
        </div>
      </div>
      <div class="mx-7 h-px bg-white/10"></div>

      <div class="reveal flex-1 overflow-y-auto px-7 py-5" style="animation-delay:.16s">
        <div class="mb-3 text-[10px] font-semibold uppercase tracking-[0.18em] text-navy-300">Domande frequenti</div>
        <div id="suggestions" class="space-y-2"></div>
      </div>

      <div class="reveal px-7 pb-7 pt-3" style="animation-delay:.22s">
        <button id="reset" class="group flex w-full items-center justify-center gap-2 rounded-xl bg-white/5 px-4 py-2.5 text-sm font-medium ring-1 ring-white/10 transition hover:bg-white/10">
          ${ICONS.reset}<span>Nuova conversazione</span>
        </button>
      </div>
    </aside>

    <!-- CHAT -->
    <main class="relative flex min-w-0 flex-1 flex-col">
      <!-- BARRA SWITCH UTENTE — sopra l'header, con il colore della colonna sinistra (navy) -->
      <div class="rail-texture relative flex items-center gap-4 bg-navy-900 px-7 py-3 text-white">
        <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400/60 to-transparent"></div>
        <span class="shrink-0 text-[10px] font-semibold uppercase tracking-[0.18em] text-navy-300">Profilo attivo</span>
        <div id="role-dd" class="relative shrink-0">
          <button id="role-btn" type="button" aria-haspopup="listbox" aria-expanded="false"
            class="flex items-center gap-2.5 rounded-xl bg-white/[0.06] py-2 pl-2.5 pr-3 text-sm font-medium text-white ring-1 ring-white/10 transition hover:bg-white/[0.09] focus:outline-none focus:ring-2 focus:ring-azure-400/50">
            <span class="grid h-5 w-5 place-items-center rounded-md bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${ICONS.user}</span>
            <span id="role-label" class="min-w-[78px] text-left">Pre-Sales</span>
            <span id="role-caret" class="text-[11px] text-navy-300 transition-transform duration-200">▾</span>
          </button>
          <div id="role-panel"
            class="absolute left-0 top-full z-30 mt-2 w-[284px] origin-top overflow-hidden rounded-2xl border border-white/10 bg-navy-850 opacity-0 scale-95 pointer-events-none shadow-2xl shadow-navy-950/70 transition duration-150 ease-out">
            <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400/50 to-transparent"></div>
            <ul id="role-list" role="listbox" class="p-1.5"></ul>
          </div>
        </div>
        <p id="role-hint" class="hidden truncate text-[11px] leading-snug text-navy-300 sm:block"></p>
      </div>

      <header class="flex items-center gap-3 border-b border-haze bg-white/70 px-7 py-3.5 backdrop-blur">
        <div class="grid h-8 w-8 place-items-center rounded-lg bg-navy-900 text-azure-300">${ICONS.shield}</div>
        <div class="min-w-0">
          <div class="text-sm font-semibold leading-tight">Assistente Pre-Sales</div>
          <div class="text-[11px] text-slatev">Risposte grounded · fonti citate · governance attiva</div>
        </div>
        <div class="ml-auto hidden items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200 sm:flex">
          <span class="h-1.5 w-1.5 rounded-full bg-emerald-500"></span> Operativo
        </div>
      </header>

      <div id="messages" class="flex-1 overflow-y-auto px-6 py-7"></div>

      <div class="border-t border-haze bg-white/70 px-6 py-4 backdrop-blur">
        <form id="form" class="mx-auto flex max-w-3xl items-center gap-2.5 rounded-2xl border border-haze bg-white px-3 py-2 shadow-sm transition focus-within:border-azure-400 focus-within:shadow-md focus-within:ring-4 focus-within:ring-azure-500/10">
          <span class="pl-1 text-slatev">${ICONS.search}</span>
          <input id="input" autocomplete="off" placeholder="Chiedi su autovelox, ZTL, omologazioni, decreti…"
            class="flex-1 bg-transparent py-1.5 text-sm placeholder:text-slatev/60 focus:outline-none" />
          <button id="send" class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-navy-900 text-white transition hover:bg-navy-700 active:scale-95 disabled:opacity-40">${ICONS.send}</button>
        </form>
        <p class="mx-auto mt-2 max-w-3xl text-center text-[10.5px] text-slatev/80">Risponde solo sui documenti aziendali e cita le fonti · verifica sempre i dati critici.</p>
      </div>
    </main>
  </div>`

  const sugg = $('#suggestions')
  for (const q of SUGGESTIONS) {
    const b = el(`<button class="w-full rounded-xl bg-white/5 px-3.5 py-2.5 text-left text-[12.5px] leading-snug text-white/90 ring-1 ring-white/10 transition hover:bg-white/[0.09] hover:ring-azure-400/40">${esc(q)}</button>`)
    b.addEventListener('click', () => ask(q))
    sugg.appendChild(b)
  }
  $('#form').addEventListener('submit', (e) => {
    e.preventDefault()
    const q = $<HTMLInputElement>('#input').value.trim()
    if (q) ask(q)
  })
  $('#reset').addEventListener('click', reset)
  greet()
}

function greet() {
  messages().innerHTML = `
  <div class="msg-in mx-auto flex max-w-3xl flex-col items-center py-16 text-center">
    <div class="grid h-14 w-14 place-items-center rounded-2xl bg-navy-900 text-azure-300 shadow-lg shadow-navy-900/25">${ICONS.pulse}</div>
    <h1 class="font-display mt-5 text-[32px] leading-tight tracking-tight text-ink">Come posso aiutarti?</h1>
    <p class="mt-2.5 max-w-md text-sm leading-relaxed text-slatev">Assistente RAG sui sistemi di Traffic Enforcement di Engine SpA. Risponde <b class="text-ink">solo</b> sulla documentazione aziendale e <b class="text-ink">cita sempre le fonti</b>.</p>
  </div>`
}

function addUser(text: string) {
  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-1 flex max-w-3xl justify-end">
      <div class="max-w-[80%] rounded-2xl rounded-tr-md bg-navy-800 px-4 py-2.5 text-sm leading-relaxed text-white shadow-sm">${esc(text)}</div>
    </div>`))
  scrollDown()
}

function thinking(): HTMLElement {
  const node = el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="flex items-center gap-2.5 rounded-2xl rounded-tl-md border border-haze bg-white px-4 py-3 text-sm text-slatev shadow-sm">
        <span class="flex gap-1">
          <span class="dot h-1.5 w-1.5 rounded-full bg-navy-500" style="animation-delay:0s"></span>
          <span class="dot h-1.5 w-1.5 rounded-full bg-navy-500" style="animation-delay:.15s"></span>
          <span class="dot h-1.5 w-1.5 rounded-full bg-navy-500" style="animation-delay:.3s"></span>
        </span>
        Ricerca nei documenti…
      </div>
    </div>`)
  messages().appendChild(node)
  scrollDown()
  return node
}

function addAssistant(r: QueryResponse) {
  const st = r.ambiguous
    ? { bar: 'bg-amber-500', pill: 'bg-amber-50 text-amber-700 ring-amber-200', label: '⚠ Ambiguo — verifica fonti', meter: 'bg-amber-500' }
    : r.grounded
      ? { bar: 'bg-emerald-500', pill: 'bg-emerald-50 text-emerald-700 ring-emerald-200', label: 'Grounded', meter: 'bg-emerald-500' }
      : { bar: 'bg-slate-400', pill: 'bg-slate-100 text-slate-600 ring-slate-200', label: 'Fuori ambito', meter: 'bg-slate-400' }
  const pct = Math.max(6, Math.min(100, Math.round(((r.top_score - 0.7) / 0.25) * 100)))
  const sources = r.sources.length
    ? `<details class="group mt-3">
         <summary class="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-navy-600 select-none">
           ${ICONS.doc}<span>Fonti citate (${r.sources.length})</span>
           <span class="ml-0.5 text-slatev transition group-open:rotate-90">›</span>
         </summary>
         <ul class="mt-2 space-y-1.5 border-l-2 border-haze pl-3">
           ${r.sources.map((s) => `<li class="flex items-start gap-2 text-xs text-slatev"><span class="mt-px text-navy-300">${ICONS.file}</span><span class="font-mono">${esc(s)}</span></li>`).join('')}
         </ul>
       </details>`
    : ''
  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="relative w-full max-w-[88%] overflow-hidden rounded-2xl rounded-tl-md border border-haze bg-white px-4 py-3.5 shadow-sm">
        <span class="absolute inset-y-0 left-0 w-[3px] ${st.bar}"></span>
        <div class="mb-1.5 flex items-center gap-2">
          <span class="font-display text-sm font-semibold text-navy-800">Assistente</span>
          <span class="rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${st.pill}">${st.label}</span>
          <span class="ml-auto font-mono text-[10px] text-slatev">conf ${r.top_score.toFixed(2)}</span>
        </div>
        <div class="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink">${esc(r.response)}</div>
        <div class="meter mt-2.5 h-1 overflow-hidden rounded-full bg-haze"><span class="block h-full rounded-full ${st.meter}" style="width:${pct}%"></span></div>
        ${sources}
      </div>
    </div>`))
  scrollDown()
}

function addError(msg: string) {
  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="w-full max-w-[88%] rounded-2xl rounded-tl-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">⚠️ ${esc(msg)}</div>
    </div>`))
  scrollDown()
}

async function ask(question: string) {
  $<HTMLInputElement>('#input').value = ''
  addUser(question)
  const node = thinking()
  const send = $<HTMLButtonElement>('#send')
  send.disabled = true
  try {
    const res = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history, role: currentRole, session_id: sessionId, user_id: userId }),
    })
    node.remove()
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      addError(err.detail || `Errore ${res.status}`)
      return
    }
    const data: QueryResponse = await res.json()
    addAssistant(data)
    history.push({ role: 'user', content: question })
    history.push({ role: 'assistant', content: data.response })
  } catch (e) {
    node.remove()
    addError(String(e))
  } finally {
    send.disabled = false
    $<HTMLInputElement>('#input').focus()
  }
}

function reset() {
  history.length = 0
  greet()
}

async function loadStatus() {
  try {
    const s = await (await fetch('/api/status')).json()
    $('#kb').innerHTML = tile('Documenti', s.documents) + tile('Chunk', s.chunks)
    $('.model-name').textContent = s.model
  } catch {
    $('.model-name').textContent = 'backend offline'
  }
}

async function loadRoles() {
  let roles: { key: string; name: string }[] = [
    { key: 'sales', name: 'Sales' },
    { key: 'presales', name: 'Pre-Sales' },
    { key: 'bid_manager', name: 'Bid Manager' },
  ]
  try {
    roles = await (await fetch('/api/roles')).json()
  } catch { /* keep fallback */ }
  if (!ROLE_HINTS[currentRole]) currentRole = 'presales'

  const dd = $('#role-dd')
  const btn = $<HTMLButtonElement>('#role-btn')
  const panel = $('#role-panel')
  const list = $('#role-list')
  const label = $('#role-label')
  const caret = $('#role-caret')
  const hint = $('#role-hint')
  const nameOf = (k: string) => roles.find((r) => r.key === k)?.name ?? k

  list.innerHTML = roles
    .map((r) => `
      <li>
        <button type="button" role="option" data-role="${r.key}"
          class="relative flex w-full items-start gap-3 rounded-xl px-3 py-2.5 text-left transition hover:bg-white/[0.06]">
          <span class="role-accent absolute inset-y-2 left-0 w-[3px] rounded-full bg-azure-400 opacity-0 transition-opacity"></span>
          <span class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-white/[0.06] text-azure-300 ring-1 ring-white/10">${ICONS.user}</span>
          <span class="min-w-0 flex-1">
            <span class="block text-sm font-medium text-white">${esc(r.name)}</span>
            <span class="block text-[11px] leading-snug text-navy-300">${esc(ROLE_HINTS[r.key] ?? '')}</span>
          </span>
          <span class="role-check mt-0.5 shrink-0 text-azure-400 opacity-0 transition-opacity">${ICONS.check}</span>
        </button>
      </li>`)
    .join('')

  const paint = () => {
    label.textContent = nameOf(currentRole)
    hint.textContent = ROLE_HINTS[currentRole] ?? ''
    list.querySelectorAll<HTMLElement>('[data-role]').forEach((opt) => {
      const active = opt.dataset.role === currentRole
      opt.classList.toggle('bg-white/[0.06]', active)
      opt.classList.toggle('ring-1', active)
      opt.classList.toggle('ring-azure-400/25', active)
      opt.setAttribute('aria-selected', String(active))
      opt.querySelector('.role-check')!.classList.toggle('opacity-0', !active)
      opt.querySelector('.role-accent')!.classList.toggle('opacity-0', !active)
    })
  }

  const open = (o: boolean) => {
    panel.classList.toggle('opacity-0', !o)
    panel.classList.toggle('scale-95', !o)
    panel.classList.toggle('pointer-events-none', !o)
    btn.setAttribute('aria-expanded', String(o))
    caret.style.transform = o ? 'rotate(180deg)' : ''
  }

  list.querySelectorAll<HTMLButtonElement>('[data-role]').forEach((opt) => {
    opt.addEventListener('click', () => {
      currentRole = opt.dataset.role!
      localStorage.setItem('np_role', currentRole)
      paint()
      open(false)
    })
  })
  btn.addEventListener('click', (e) => {
    e.stopPropagation()
    open(panel.classList.contains('opacity-0'))
  })
  document.addEventListener('click', (e) => {
    if (!dd.contains(e.target as Node)) open(false)
  })
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') open(false) })

  paint()
}

render()
loadStatus()
loadRoles()
