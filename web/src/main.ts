import './style.css'
import { initBandi } from './bandi'
import { initConfig } from './config'

interface QueryResponse {
  query: string
  standalone_query: string
  response: string
  sources: string[]
  source_links?: (string | null)[]
  context: string[]
  model: string
  grounded: boolean
  ambiguous: boolean
  top_score: number
  role?: string | null
  confidence?: string | null
  pii_masked?: number | null
}
type ChatMessage = { role: 'user' | 'assistant'; content: string }

const history: ChatMessage[] = []
// Full turns (question + response) kept for the conversation export (RF16).
const transcript: { q: string; r: QueryResponse }[] = []

const SUGGESTIONS = [
  'Quali requisiti per l’omologazione degli autovelox?',
  'Come funziona la gestione di una ZTL?',
  'Differenza tra approvazione e omologazione di un rilevatore?',
  'Cosa prevede il Codice della Strada sui limiti di velocità?',
]

// Ordine = priorità del profilo (basso → alto): Pre-Sales · Sales · Bid Manager.
const ROLE_HINTS: Record<string, string> = {
  presales: 'Tecnico · parametri e specifiche · fonti inline',
  sales: 'Linguaggio cliente · benefici · risposte brevi',
  bid_manager: 'Conformità · riferimenti normativi · adempimenti',
}
let currentRole = localStorage.getItem('np_role') || 'presales'
let roleLocked = false  // true when auth is on: the role comes from the verified session

// Operator shown in the top-right profile chip. Change here to personalize.
const USER_NAME = 'User'

// ── Theme (light/dark) ───────────────────────────────────────────────────────
// Stored in localStorage; falls back to the OS preference on first visit.
function preferredTheme(): 'light' | 'dark' {
  const saved = localStorage.getItem('np_theme')
  if (saved === 'light' || saved === 'dark') return saved
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
function applyTheme(theme: 'light' | 'dark') {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  localStorage.setItem('np_theme', theme)
  const btn = document.querySelector('#theme-btn')
  if (btn) {
    btn.innerHTML = theme === 'dark' ? ICONS.sun : ICONS.moon
    btn.setAttribute('title', theme === 'dark' ? 'Passa al tema chiaro' : 'Passa al tema scuro')
  }
}
// Apply before first paint to avoid a flash of the wrong theme.
applyTheme(preferredTheme())

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
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M5 21h14"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3 w-3"><rect x="4" y="11" width="16" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  quote: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-3.5 w-3.5"><path d="M7 7h4v4H7z"/><path d="M13 7h4v4h-4z"/><path d="M7 11c0 3-1 4-3 5"/><path d="M13 11c0 3-1 4-3 5"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-[18px] w-[18px]"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-[18px] w-[18px]"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
}

// RF17 — declared system limits, surfaced in the UI (governance / "cosa NON fa").
const LIMITS = [
  'Risponde <b>solo</b> sulla documentazione aziendale indicizzata: nessuna conoscenza esterna.',
  'Non garantisce prezzi, sconti o condizioni commerciali — vanno verificati con il Bid Manager.',
  'In caso di fonti in conflitto non interpreta né decide: rimanda al Bid Manager (discrezione).',
  'Non determina la vigenza o l’abrogazione di decreti e normative.',
  'I dati possono essere sintetici o modificati: verifica sempre i dati critici (gare, offerte).',
  'Non sostituisce il parere legale o tecnico ufficiale.',
]

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
// Minimal, XSS-safe markdown rendering: escape first (so any HTML in the text becomes
// inert), THEN turn **bold** into <strong>. Only our own tags can reach innerHTML.
function renderRich(s: string): string {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}
const tile = (label: string, val: string | number) =>
  `<div class="stat-tile">
     <div class="stat-num">${val}</div>
     <div class="stat-lbl">${label}</div>
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
            <div class="font-display text-[26px] leading-none tracking-tight">Sentinel</div>
            <div class="mt-1.5 text-[10px] uppercase tracking-[0.2em] text-navy-300">Sales Assistant · Engine SpA</div>
          </div>
        </div>
      </div>
      <div class="mx-7 h-px bg-white/10"></div>

      <!-- VIEW SWITCH -->
      <nav class="reveal px-5 py-4" style="animation-delay:.06s">
        <button id="nav-chat" data-view="chat" class="nav-item group mb-1.5 flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-white/90 transition">
          <span class="nav-ico grid h-7 w-7 place-items-center rounded-lg bg-white/[0.06] text-azure-300 ring-1 ring-white/10">${ICONS.pulse}</span>
          <span id="nav-chat-label">Assistente Pre-Sales</span>
        </button>
        <button id="nav-bandi" data-view="bandi" class="nav-item group mb-1.5 flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-white/90 transition">
          <span class="nav-ico grid h-7 w-7 place-items-center rounded-lg bg-white/[0.06] text-azure-300 ring-1 ring-white/10">${ICONS.search}</span>
          <span>Gare d'Appalto · MIT</span>
        </button>
        <button id="nav-config" data-view="config" class="nav-item group flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-white/90 transition">
          <span class="nav-ico grid h-7 w-7 place-items-center rounded-lg bg-white/[0.06] text-azure-300 ring-1 ring-white/10"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-5 w-5"><path d="M15 4V2M15 10V8M11 6H9M21 6h-2M18 9l-1.5-1.5M18 3l-1.5 1.5M4 20l9-9M13.5 6.5 17 10"/></svg></span>
          <span>Configura Offerta</span>
        </button>
      </nav>

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

      <div class="reveal space-y-2 px-7 pb-7 pt-3" style="animation-delay:.22s">
        <div class="flex gap-2">
          <button id="export" class="group flex flex-1 items-center justify-center gap-2 rounded-xl bg-white/5 px-3 py-2.5 text-sm font-medium ring-1 ring-white/10 transition hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-white/5" title="Esporta la conversazione in Markdown">
            ${ICONS.download}<span>Esporta</span>
          </button>
          <button id="limits-btn" class="group flex items-center justify-center gap-2 rounded-xl bg-white/5 px-3 py-2.5 text-sm font-medium ring-1 ring-white/10 transition hover:bg-white/10" title="Limiti del sistema">
            ${ICONS.info}<span>Limiti</span>
          </button>
        </div>
        <button id="reset" class="group flex w-full items-center justify-center gap-2 rounded-xl bg-white/5 px-4 py-2.5 text-sm font-medium ring-1 ring-white/10 transition hover:bg-white/10">
          ${ICONS.reset}<span>Nuova conversazione</span>
        </button>
      </div>
    </aside>

    <!-- CHAT -->
    <main id="chat-main" class="relative flex min-w-0 flex-1 flex-col">
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

        <div class="ml-auto flex shrink-0 items-center gap-2.5">
          <button id="theme-btn" type="button" aria-label="Cambia tema"
            class="grid h-9 w-9 place-items-center rounded-xl bg-white/[0.06] text-navy-200 ring-1 ring-white/10 transition hover:bg-white/[0.1] hover:text-white focus:outline-none focus:ring-2 focus:ring-azure-400/50"></button>
          <div class="flex items-center gap-2.5 rounded-xl bg-white/[0.06] py-1.5 pl-1.5 pr-3 ring-1 ring-white/10">
            <span class="grid h-7 w-7 place-items-center rounded-lg bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${ICONS.user}</span>
            <div class="leading-tight">
              <div class="text-[13px] font-medium text-white">${esc(USER_NAME)}</div>
              <div class="text-[9.5px] uppercase tracking-[0.14em] text-navy-300">Engine SpA</div>
            </div>
          </div>
        </div>
      </div>

      <div id="messages" class="flex-1 overflow-y-auto px-6 py-7"></div>

      <div class="border-t border-haze bg-card/80 px-6 py-4 backdrop-blur-md">
        <form id="form" class="mx-auto flex max-w-3xl items-center gap-2.5 rounded-2xl border border-haze bg-card px-3 py-2 shadow-lg shadow-navy-950/20 transition-all focus-within:border-azure-400/50 focus-within:shadow-azure-500/5 focus-within:ring-3 focus-within:ring-azure-500/10">
          <span class="pl-1 text-azure-400/60">${ICONS.search}</span>
          <input id="input" autocomplete="off" placeholder="Chiedi su autovelox, ZTL, omologazioni, decreti…"
            class="flex-1 bg-transparent py-2 text-[13.5px] placeholder:text-slatev/50 focus:outline-none" />
          <button id="send" class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-azure-500 text-white shadow-sm transition hover:bg-azure-600 active:scale-95 disabled:opacity-35">${ICONS.send}</button>
        </form>
        <p class="mx-auto mt-2 max-w-3xl text-center font-mono text-[9.5px] uppercase tracking-[0.14em] text-slatev/60">Risponde solo sui documenti · cita le fonti · verifica i dati critici</p>
      </div>
    </main>

    <!-- GARE D'APPALTO (Portale Appalti MIT) — separate section, initialized lazily -->
    <main id="bandi-main" class="relative hidden min-w-0 flex-1 flex-col"></main>

    <!-- CONFIGURA OFFERTA (bozza grounded) — separate section, initialized lazily -->
    <main id="config-main" class="relative hidden min-w-0 flex-1 flex-col"></main>

    <!-- RF17 — LIMITI DEL SISTEMA (modal) -->
    <div id="limits-modal" class="fixed inset-0 z-50 hidden items-center justify-center p-4">
      <div id="limits-backdrop" class="absolute inset-0 bg-navy-950/60 backdrop-blur-sm"></div>
      <div class="relative w-full max-w-lg overflow-hidden rounded-2xl border border-white/10 bg-navy-900 text-white shadow-2xl shadow-navy-950/70">
        <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400 to-transparent"></div>
        <div class="flex items-center gap-3 px-6 pt-6">
          <div class="grid h-9 w-9 place-items-center rounded-xl bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${ICONS.shield}</div>
          <div>
            <div class="font-display text-lg leading-none">Limiti del sistema</div>
            <div class="mt-1 text-[10px] uppercase tracking-[0.18em] text-navy-300">Governance · cosa NON fa</div>
          </div>
          <button id="limits-close" class="ml-auto grid h-8 w-8 place-items-center rounded-lg text-navy-300 transition hover:bg-white/10 hover:text-white" aria-label="Chiudi">✕</button>
        </div>
        <ul class="space-y-2.5 px-6 py-5 text-[13px] leading-relaxed text-navy-100">
          ${LIMITS.map((l) => `<li class="flex gap-2.5"><span class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-azure-400"></span><span>${l}</span></li>`).join('')}
        </ul>
        <div class="border-t border-white/10 bg-white/[0.03] px-6 py-3 text-[11px] text-navy-300">Trasparenza dichiarata · le fonti sono verificabili ma non garantite.</div>
      </div>
    </div>

    <!-- C2 — LOGIN (mostrato solo se l'autenticazione è abilitata) -->
    <div id="login-overlay" class="fixed inset-0 z-[60] hidden items-center justify-center p-4">
      <div class="absolute inset-0 bg-navy-950/80 backdrop-blur-md"></div>
      <form id="login-form" class="relative w-full max-w-sm overflow-hidden rounded-2xl border border-white/10 bg-navy-900 text-white shadow-2xl shadow-navy-950/70">
        <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-azure-400 to-transparent"></div>
        <div class="flex items-center gap-3 px-6 pt-6">
          <div class="grid h-10 w-10 place-items-center rounded-xl bg-azure-500/15 text-azure-300 ring-1 ring-azure-400/30">${ICONS.pulse}</div>
          <div>
            <div class="font-display text-xl leading-none">NextPulse</div>
            <div class="mt-1 text-[10px] uppercase tracking-[0.18em] text-navy-300">Accesso · il ruolo è assegnato dal profilo</div>
          </div>
        </div>
        <div class="space-y-3 px-6 py-5">
          <input id="login-user" autocomplete="username" placeholder="Utente" class="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-white placeholder:text-navy-300 focus:border-azure-400/50 focus:outline-none focus:ring-2 focus:ring-azure-500/20" />
          <input id="login-pass" type="password" autocomplete="current-password" placeholder="Password" class="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-sm text-white placeholder:text-navy-300 focus:border-azure-400/50 focus:outline-none focus:ring-2 focus:ring-azure-500/20" />
          <p id="login-error" class="hidden text-[12px] text-red-400"></p>
          <button type="submit" class="w-full rounded-xl bg-azure-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-azure-500/20 transition hover:bg-azure-600 active:scale-95">Accedi</button>
          <p class="text-center text-[10.5px] text-navy-300">Demo: <span class="font-mono">bid / bid123</span> · <span class="font-mono">presales / presales123</span> · <span class="font-mono">sales / sales123</span></p>
        </div>
      </form>
    </div>
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
  $('#export').addEventListener('click', exportConversation)

  const modal = $('#limits-modal')
  const toggleLimits = (open: boolean) => {
    modal.classList.toggle('hidden', !open)
    modal.classList.toggle('flex', open)
  }
  $('#limits-btn').addEventListener('click', () => toggleLimits(true))
  $('#limits-close').addEventListener('click', () => toggleLimits(false))
  $('#limits-backdrop').addEventListener('click', () => toggleLimits(false))
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') toggleLimits(false) })

  $('#theme-btn').addEventListener('click', () => {
    applyTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark')
  })
  applyTheme(preferredTheme())  // set the button icon now that it exists

  // View switch: chat ↔ Gare d'Appalto (Portale Appalti MIT) ↔ Configura Offerta.
  $('#nav-chat').addEventListener('click', () => showView('chat'))
  $('#nav-bandi').addEventListener('click', () => showView('bandi'))
  $('#nav-config').addEventListener('click', () => showView('config'))
  showView('chat')

  updateExportState()
  greet()
}

let bandiReady = false
let configReady = false
function showView(view: 'chat' | 'bandi' | 'config') {
  const panes: Record<string, HTMLElement> = {
    chat: $('#chat-main'), bandi: $('#bandi-main'), config: $('#config-main'),
  }
  for (const [name, pane] of Object.entries(panes)) {
    const active = name === view
    pane.classList.toggle('hidden', !active)
    pane.classList.toggle('flex', active)
  }
  $('#nav-chat').classList.toggle('nav-active', view === 'chat')
  $('#nav-bandi').classList.toggle('nav-active', view === 'bandi')
  $('#nav-config').classList.toggle('nav-active', view === 'config')
  if (view === 'bandi' && !bandiReady) { bandiReady = true; initBandi(panes.bandi) }
  if (view === 'config' && !configReady) { configReady = true; initConfig(panes.config) }
}

// Enable the export button only once there is something to export.
function updateExportState() {
  const btn = $<HTMLButtonElement>('#export')
  if (btn) btn.disabled = transcript.length === 0
}

function greet() {
  messages().innerHTML = `
  <div class="msg-in mx-auto flex max-w-3xl flex-col items-center justify-center py-20 text-center">

    <!-- Animated logo with pulse rings -->
    <div class="pulse-ring greet-logo mb-8">
      <div class="relative z-10 grid h-16 w-16 place-items-center rounded-2xl bg-navy-850 text-azure-400 shadow-2xl shadow-navy-950/60 ring-1 ring-azure-400/20">
        ${ICONS.pulse}
      </div>
    </div>

    <!-- Heading -->
    <div class="mb-1 font-mono text-[10px] uppercase tracking-[0.28em] text-azure-400/70">Sentinel · Engine SpA · Traffic Enforcement</div>
    <h1 class="font-display mt-3 text-[36px] leading-[1.1] tracking-tight text-ink">Come posso aiutarti?</h1>
    <p class="mt-3 max-w-sm text-[13.5px] leading-relaxed text-slatev">
      Risponde <span class="font-semibold text-ink">solo</span> sulla documentazione aziendale e
      <span class="font-semibold text-ink">cita sempre le fonti</span>.
      Se non è nei documenti, lo dice.
    </p>

    <!-- Quick chips grid -->
    <div class="mt-10 grid w-full max-w-xl grid-cols-2 gap-2.5 px-2">
      ${SUGGESTIONS.map((q) =>
        `<button class="greet-chip rounded-xl border border-haze bg-card/60 px-4 py-3 text-left text-[12px] leading-snug text-ink/80 backdrop-blur transition hover:border-azure-400/30 hover:bg-card hover:text-ink" onclick="this.dispatchEvent(new CustomEvent('greet-ask',{bubbles:true,detail:'${q.replace(/'/g,"\\'")}'}))">
           <span class="mb-1 block font-mono text-[9px] uppercase tracking-[0.15em] text-azure-400/60">suggerimento</span>
           ${esc(q)}
         </button>`
      ).join('')}
    </div>
  </div>`

  // wire up the chip buttons via event delegation
  messages().addEventListener('greet-ask', (e) => {
    const q = (e as CustomEvent).detail as string
    if (q) ask(q)
  }, { once: true })
}

function addUser(text: string) {
  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-2 flex max-w-3xl justify-end">
      <div class="max-w-[78%] rounded-2xl rounded-tr-sm bg-navy-700 px-4 py-3 text-[13.5px] leading-relaxed text-white ring-1 ring-azure-400/10 shadow-lg shadow-navy-950/20">
        ${esc(text)}
      </div>
    </div>`))
  scrollDown()
}

function thinking(): HTMLElement {
  const node = el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="flex items-center gap-3 rounded-2xl rounded-tl-sm border border-haze bg-card/80 px-4 py-3 text-[12px] text-slatev backdrop-blur-sm">
        <span class="relative flex h-2 w-2 shrink-0">
          <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-azure-400/60"></span>
          <span class="relative inline-flex h-2 w-2 rounded-full bg-azure-400"></span>
        </span>
        <span class="font-mono tracking-wide">Ricerca nei documenti<span class="blink-cursor">▋</span></span>
      </div>
    </div>`)
  messages().appendChild(node)
  scrollDown()
  return node
}

// Map a retrieval cosine (≈0.70–1.00) to a 1–8 segment level (used only for grounded answers).
function scoreToLevel(score: number): number {
  return Math.round(Math.max(1, Math.min(8, ((score - 0.70) / 0.30) * 8)))
}

// The segment bar shows the GOVERNANCE trust level (0–8), not the raw similarity: an
// ambiguous/deferred answer must read as low-trust, never as a confident one.
function confSegs(level: number, colorClass: string): string {
  const lit = Math.max(0, Math.min(8, Math.round(level)))
  return `<div class="conf-segs ${colorClass}">
    ${Array.from({ length: 8 }, (_, i) =>
      `<span class="conf-seg${i < lit ? ' lit' : ''}"></span>`
    ).join('')}
  </div>`
}

function addAssistant(r: QueryResponse) {
  // Color + segment level follow the GOVERNANCE outcome (confidence 🟢/🟡/🔴), not the raw
  // top_score: an ambiguous deferral has a high similarity but must NOT look confident.
  const lvl = scoreToLevel(r.top_score)
  const st = r.ambiguous
    ? { borderColor: 'border-l-[var(--color-signal-red)]', lozenge: 'lozenge-red', label: 'Ambiguo', sublabel: 'fonti in conflitto — rimanda al Bid Manager', segsColor: 'text-[var(--color-signal-red)]', level: 2 }
    : !r.grounded
      ? { borderColor: 'border-l-[var(--color-signal-slate)]', lozenge: 'lozenge-slate', label: 'Fuori ambito', sublabel: 'nessuna fonte rilevante', segsColor: 'text-[var(--color-signal-slate)]', level: 1 }
      : r.confidence === 'yellow'
        ? { borderColor: 'border-l-[var(--color-signal-amber)]', lozenge: 'lozenge-amber', label: 'Grounded', sublabel: 'più fonti — verifica i dati critici', segsColor: 'text-[var(--color-signal-amber)]', level: Math.min(lvl, 6) }
        : { borderColor: 'border-l-[var(--color-signal-green)]', lozenge: 'lozenge-green', label: 'Grounded', sublabel: 'fonte diretta', segsColor: 'text-[var(--color-signal-green)]', level: lvl }

  // Numbered sources
  const sources = r.sources.length
    ? `<div class="mt-4 border-t border-haze pt-3.5">
         <div class="mb-2 flex items-center gap-2 text-[9.5px] font-mono uppercase tracking-[0.18em] text-slatev">
           ${ICONS.doc}<span>Fonti citate</span>
         </div>
         ${r.sources.map((s, i) => {
           const url = r.source_links?.[i]
           const txt = url
             ? `<a href="${esc(url)}" target="_blank" rel="noopener" class="source-ref-text underline decoration-dotted underline-offset-2 transition-colors hover:text-azure-400" title="Apri la fonte ufficiale su mit.gov.it">${esc(s)} ↗</a>`
             : `<span class="source-ref-text">${esc(s)}</span>`
           return `<div class="source-ref text-slatev"><span class="source-ref-num">[${i + 1}]</span>${txt}</div>`
         }).join('')}
       </div>`
    : ''

  // RF14 — chunk excerpts
  const excerpts = (r.context?.length && (r.grounded || r.ambiguous))
    ? `<details class="group mt-3">
         <summary class="flex cursor-pointer list-none items-center gap-1.5 text-[10px] font-mono uppercase tracking-[0.15em] text-slatev select-none hover:text-ink/70 transition-colors">
           ${ICONS.quote}<span>Estratti (${r.context.length})</span>
           <span class="ml-0.5 transition-transform group-open:rotate-90">›</span>
         </summary>
         <div class="mt-2.5 space-y-2">
           ${r.context.map((c) =>
             `<div class="excerpt-block bg-haze/30 text-slatev">${esc(c.length > 320 ? c.slice(0, 320).trimEnd() + '…' : c)}</div>`
           ).join('')}
         </div>
       </details>`
    : ''

  // GDPR badge
  const piiBadge = (r.pii_masked ?? 0) > 0
    ? `<span class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[9.5px] text-violet-400 bg-violet-400/10 ring-1 ring-violet-400/20" title="Entità PII pseudonimizzate localmente (GDPR Art. 32)">${ICONS.lock} ${r.pii_masked} PII</span>`
    : ''

  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="resp-card border-l-[3px] ${st.borderColor} border border-haze bg-card/90 px-5 py-4 shadow-lg shadow-navy-950/20 backdrop-blur-sm">

        <!-- Header: status + confidence -->
        <div class="mb-3.5 flex items-center gap-3 flex-wrap">
          <div class="status-lozenge ${st.lozenge}">
            <span class="lozenge-dot"></span>
            ${st.label}
            ${st.sublabel ? `<span class="opacity-60">— ${st.sublabel}</span>` : ''}
          </div>
          ${piiBadge}
          <div class="ml-auto flex items-center gap-2.5">
            ${confSegs(st.level, st.segsColor)}
            <span class="font-mono text-[10px] text-slatev tabular-nums" title="Similarità di retrieval">${r.top_score.toFixed(2)}</span>
          </div>
        </div>

        <!-- Response body -->
        <div class="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink">${renderRich(r.response)}</div>

        <!-- Numbered sources -->
        ${sources}

        <!-- Excerpts (collapsible) -->
        ${excerpts}
      </div>
    </div>`))
  scrollDown()
}

function addError(msg: string) {
  messages().appendChild(el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="w-full max-w-[88%] rounded-xl rounded-tl-sm border border-red-400/25 bg-red-400/10 px-4 py-3 font-mono text-[12px] text-red-400">
        ⚠ ${esc(msg)}
      </div>
    </div>`))
  scrollDown()
}

// Lightweight bubble shown while tokens stream in; replaced by the full card on `done`.
function addLive(): HTMLElement {
  const node = el(`
    <div class="msg-in mx-auto mt-3 flex max-w-3xl">
      <div class="resp-card border-l-[3px] border-l-[var(--color-signal-green)] border border-haze bg-card/90 px-5 py-4 shadow-lg shadow-navy-950/20 backdrop-blur-sm">
        <div class="live-body whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink"></div>
      </div>
    </div>`)
  messages().appendChild(node)
  scrollDown()
  return node
}

function setLive(node: HTMLElement, text: string) {
  node.querySelector<HTMLElement>('.live-body')!.innerHTML =
    renderRich(text) + '<span class="blink-cursor">▋</span>'
}

// Streaming ask (SSE): tokens appear live, then the final card (status + sources + excerpts)
// replaces the live bubble. Same payload/contract as /api/query — only the delivery differs.
async function ask(question: string) {
  $<HTMLInputElement>('#input').value = ''
  addUser(question)
  const node = thinking()
  const send = $<HTMLButtonElement>('#send')
  send.disabled = true
  let live: HTMLElement | null = null
  let acc = ''
  let thinkingGone = false
  const dropThinking = () => { if (!thinkingGone) { node.remove(); thinkingGone = true } }
  try {
    const res = await fetch('/api/query/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history, role: currentRole, session_id: sessionId, user_id: userId }),
    })
    if (!res.ok || !res.body) {
      dropThinking()
      const err = await res.json().catch(() => ({}))
      addError(err.detail || `Errore ${res.status}`)
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let done: QueryResponse | null = null
    let failed: { message?: string } | null = null
    for (;;) {
      const { value, done: rdone } = await reader.read()
      if (rdone) break
      buf += decoder.decode(value, { stream: true })
      let i: number
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, i).trim()
        buf = buf.slice(i + 2)
        if (!frame.startsWith('data:')) continue
        let evt: { phase: string; data: any }
        try { evt = JSON.parse(frame.slice(5).trim()) } catch { continue }
        if (evt.phase === 'token') {
          if (!live) { dropThinking(); live = addLive() }
          acc += evt.data
          setLive(live, acc)
          scrollDown()
        } else if (evt.phase === 'done') {
          done = evt.data
        } else if (evt.phase === 'error') {
          failed = evt.data || {}
        }
      }
    }
    dropThinking()
    if (live) live.remove()
    if (failed) { addError(failed.message || 'Errore di generazione'); return }
    if (done) {
      addAssistant(done)
      history.push({ role: 'user', content: question })
      history.push({ role: 'assistant', content: done.response })
      transcript.push({ q: question, r: done })
      updateExportState()
    }
  } catch (e) {
    dropThinking()
    if (live) live.remove()
    addError(String(e))
  } finally {
    send.disabled = false
    $<HTMLInputElement>('#input').focus()
  }
}

function reset() {
  history.length = 0
  transcript.length = 0
  updateExportState()
  greet()
}

// RF16 — export the conversation (questions, answers, sources, governance) as Markdown,
// a hand-off artifact for the Bid Manager. Fully client-side, no PII leaves the browser.
function exportConversation() {
  if (transcript.length === 0) return
  const ts = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const stamp = `${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}-${pad(ts.getHours())}${pad(ts.getMinutes())}`
  const roleName = $('#role-label')?.textContent?.trim() || currentRole
  const model = $('.model-name')?.textContent?.trim() || ''

  const lines: string[] = [
    '# NextPulse — Conversazione',
    '',
    `- **Data:** ${ts.toLocaleString('it-IT')}`,
    `- **Profilo:** ${roleName}`,
    model ? `- **Modello:** ${model}` : '',
    '',
    '> Esportazione automatica dell’assistente RAG di Engine SpA. Le fonti sono verificabili',
    '> ma non garantite: verifica i dati critici con il Bid Manager.',
    '',
    '---',
    '',
  ]
  transcript.forEach(({ q, r }, i) => {
    const stato = r.ambiguous ? 'Ambiguo (verifica fonti)' : r.grounded ? 'Grounded' : 'Fuori ambito'
    lines.push(`## ${i + 1}. ${q}`, '')
    lines.push(r.response, '')
    lines.push(`*Stato: ${stato} · confidenza ${r.top_score.toFixed(2)}${(r.pii_masked ?? 0) > 0 ? ` · ${r.pii_masked} PII pseudonimizzate` : ''}*`, '')
    if (r.sources.length) {
      lines.push('**Fonti:**')
      r.sources.forEach((s) => lines.push(`- ${s}`))
      lines.push('')
    }
    lines.push('---', '')
  })

  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `nextpulse-conversazione-${stamp}.md`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// C2 — when auth is enabled the role is bound to a verified session (not client-chosen).
async function initAuth() {
  let me: { auth_enabled?: boolean; authenticated?: boolean; role?: string; username?: string }
  try {
    me = await (await fetch('/api/me')).json()
  } catch {
    return  // backend unreachable → behave as before (client role)
  }
  if (!me?.auth_enabled) return            // auth off → unchanged demo flow
  if (me.authenticated && me.role) {
    applyAuth(me.role)
    return
  }
  showLogin()
}

function applyAuth(role: string) {
  currentRole = role
  localStorage.setItem('np_role', role)
  roleLocked = true
}

function showLogin() {
  const overlay = $('#login-overlay')
  overlay.classList.remove('hidden')
  overlay.classList.add('flex')
  const form = $<HTMLFormElement>('#login-form')
  const err = $('#login-error')
  form.addEventListener('submit', async (e) => {
    e.preventDefault()
    err.classList.add('hidden')
    const username = $<HTMLInputElement>('#login-user').value.trim()
    const password = $<HTMLInputElement>('#login-pass').value
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) {
        const e2 = await res.json().catch(() => ({}))
        err.textContent = e2.detail || 'Credenziali non valide'
        err.classList.remove('hidden')
        return
      }
      const data = await res.json()
      applyAuth(data.role)
      overlay.classList.add('hidden')
      overlay.classList.remove('flex')
      await loadRoles()  // repaint the (now locked) role selector with the verified role
    } catch {
      err.textContent = 'Errore di rete'
      err.classList.remove('hidden')
    }
  })
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
  // Ordine = priorità del profilo (basso → alto): Pre-Sales · Sales · Bid Manager.
  // Fallback usato solo se /api/roles non risponde; l'ordine reale arriva dal backend.
  let roles: { key: string; name: string }[] = [
    { key: 'presales', name: 'Pre-Sales' },
    { key: 'sales', name: 'Sales' },
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

  const navLabel = $('#nav-chat-label')
  const paint = () => {
    label.textContent = nameOf(currentRole)
    navLabel.textContent = `Assistente ${nameOf(currentRole)}`
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

  // When auth is on, the role is assigned by the verified session → lock the selector.
  if (roleLocked) {
    btn.disabled = true
    btn.classList.add('opacity-70', 'cursor-not-allowed')
    btn.title = 'Ruolo assegnato dal login (sicurezza)'
  }
  paint()
}

render()
loadStatus()
// Resolve auth first (may set/lock the role) so the selector paints the verified profile.
initAuth().then(loadRoles)
