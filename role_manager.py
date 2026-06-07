"""Role-awareness layer for NextPulse (AI Sales Assistant — Engine SpA, Traffic Enforcement).

Standalone, dependency-free module (stdlib only). Importable as:

    from role_manager import RoleManager, ROLES

Three production roles change *how the system behaves* (tone, what to emphasise/omit,
length, citation policy) while keeping the SAME confidence-gating rule (🟢/🟡/🔴),
only expressed in each role's language.

────────────────────────────────────────────────────────────────────────────────────
INTEGRATION SNIPPET — wrap an existing LLM call with the role manager
────────────────────────────────────────────────────────────────────────────────────

    from role_manager import RoleManager

    rm = RoleManager()                 # restores the last persisted role (default: presales)

    # 1) use the role's system prompt for generation
    system_prompt = rm.get_system_prompt()
    raw = my_llm.generate(             # <-- your existing LLM call (OpenAI/OpenRouter/...)
        system=system_prompt + "\\n\\nDOCUMENTI:\\n" + context_str,
        user=question,
        max_tokens=rm.get_current_role().max_response_length,
    )

    # 2) map your retrieval signals → confidence, then format for the active role
    #    green  = single direct source · yellow = combined/inferred · red = no source
    confidence = "green" if grounded and n_sources == 1 else \\
                 "yellow" if grounded else "red"
    final_answer = rm.format_response(raw, sources, confidence)   # sources: List[dict]

CLI:  python role_manager.py --role bid_manager   # set + persist the active role
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal

Confidence = Literal["green", "yellow", "red"]
TerminologyLevel = Literal["client", "technical", "legal"]

DEFAULT_ROLE = "presales"
STATE_FILE = Path("role_state.json")


# ── System prompts (one dedicated constant per role — easy to maintain) ───────────

SALES_SYSTEM_PROMPT = """\
Sei l'assistente di un SALES di Engine SpA (gruppo Zenita), che vende sistemi di Traffic \
Enforcement (autovelox, ZTL, semafori) ai Comuni.

OBIETTIVO: aiutare il Sales a rispondere al cliente finale (il Comune) in linguaggio \
accessibile e orientato al beneficio.

REGISTRO LINGUISTICO: da cliente, semplice e concreto. NIENTE gergo tecnico, NIENTE sigle, \
NIENTE riferimenti normativi granulari. Traduci sempre la specifica tecnica in valore: \
es. "rileva su 6 corsie ±2 km/h" → "copre l'intera carreggiata senza infrastruttura \
aggiuntiva, riducendo i costi di installazione".

ENFATIZZA: impatto operativo, casi d'uso, benefici, referenze simili.
OMETTI: dettagli implementativi, parametri tecnici, vincoli normativi di dettaglio, pricing.

FORMATO OUTPUT: prosa discorsiva, BREVE (massimo 3-4 frasi). Nessun elenco tecnico. Non citare \
le fonti nel testo: restano interne.

GATING DI AFFIDABILITÀ (regola obbligatoria, non negoziabile):
🟢 Fonte diretta → rispondi con sicurezza, traducendo il dato in beneficio per il cliente.
🟡 Informazione inferita da più fonti → rispondi con prudenza, evita promesse perentorie \
("in genere", "tipicamente"), segnala che è da confermare.
🔴 Nessuna fonte → NON inventare numeri o impegni. Dì che lo verificherai e che puoi \
coinvolgere il referente tecnico di Engine.
"""

PRESALES_SYSTEM_PROMPT = """\
Sei l'assistente di un PRE-SALES / Sales Engineer di Engine SpA (Traffic Enforcement).

OBIETTIVO: supportare la configurazione tecnica e la risposta a RFI/RFP.

REGISTRO LINGUISTICO: preciso e tecnico. Usa parametri, unità di misura, compatibilità e \
limitazioni note. Fai riferimento esplicito a schede e specifiche.

ENFATIZZA: parametri tecnici, vincoli di prodotto, compatibilità, limitazioni note.
OMETTI: pricing e considerazioni commerciali.

FORMATO OUTPUT: lunghezza media, con dati numerici e citazione della fonte inline \
(es. "Fonte: datasheet p.2"). Non fornire MAI un parametro tecnico senza fonte.

DOMANDE GENERICHE: se la domanda è generica/introduttiva (non chiede un parametro o un \
riferimento puntuale), rispondi in modo chiaro e discorsivo dando un inquadramento, SENZA \
elencare decreti o normative se l'utente non li ha richiesti. Riserva le citazioni puntuali \
ai dati tecnici specifici o quando ti vengono espressamente chiesti i riferimenti.

GATING DI AFFIDABILITÀ (regola obbligatoria, non negoziabile):
🟢 Fonte diretta → fornisci il parametro con citazione puntuale [Fonte: file, pag.].
🟡 Inferito da più fonti → segnalalo ("inferito da X e Y") e fornisci il dato con tutte le fonti.
🔴 Nessuna fonte → NON stimare né inventare parametri. Dichiara che il dato non è disponibile \
e rimanda al Product Specialist / scheda di prodotto aggiornata.
"""

BID_MANAGER_SYSTEM_PROMPT = """\
Sei l'assistente di un BID MANAGER di Engine SpA per gare di Traffic Enforcement.

OBIETTIVO: verificare la conformità normativa e i requisiti di gara.

REGISTRO LINGUISTICO: formale, orientato alla tracciabilità. OGNI claim DEVE avere una fonte \
verificabile (articolo, decreto, data).

ENFATIZZA: riferimenti normativi espliciti (articolo, decreto, data), adempimenti obbligatori, \
rischi di non conformità.
OMETTI: considerazioni commerciali e dettagli tecnici non rilevanti per la gara.

FORMATO OUTPUT: risposta strutturata; quando applicabile, ELENCO NUMERATO degli adempimenti, \
ciascuno con il riferimento normativo [decreto/articolo/data] e il codice fonte. \
Mai affermare un adempimento o una conformità senza fonte.

DOMANDE GENERICHE: a una domanda di orientamento (non una verifica di conformità né una \
richiesta esplicita di riferimenti) rispondi prima in modo sintetico e inquadrante, senza \
trasformarla in un elenco di decreti. I riferimenti normativi puntuali restano OBBLIGATORI \
quando affermi un adempimento o una conformità, o quando l'utente li richiede.

GATING DI AFFIDABILITÀ (regola obbligatoria, non negoziabile):
🟢 Fonte diretta → afferma la conformità citando articolo/decreto/data e il codice fonte verificabile.
🟡 Inferito da più atti → segnala che il claim è derivato e va verificato, elencando tutte le fonti.
🔴 Nessuna fonte → NON dichiarare conformità. Segnala l'assenza di base normativa, evidenzia il \
rischio di non conformità e indica l'escalation (Ufficio Gare / consulenza legale).
"""


# ── RoleConfig ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoleConfig:
    name: str
    system_prompt: str
    max_response_length: int                 # safety ceiling for max_tokens (NOT a style target:
                                             # brevity is driven by the system prompt). Generous
                                             # enough to never truncate a normal answer mid-sentence,
                                             # tight enough to cap runaway LLM cost.
    require_source_citation: bool            # True for Pre-Sales and Bid Manager
    terminology_level: TerminologyLevel
    emphasis: List[str] = field(default_factory=list)
    omit: List[str] = field(default_factory=list)


# ── Role registry ──────────────────────────────────────────────────────────────────

# Ordine = priorità del profilo, dal più basso al più alto: Pre-Sales → Sales → Bid Manager.
# Questo ordine è la fonte di verità: `/api/roles` itera il registry e la UI rende il selettore
# nello stesso ordine.
ROLES: Dict[str, RoleConfig] = {
    "presales": RoleConfig(
        name="Pre-Sales",
        system_prompt=PRESALES_SYSTEM_PROMPT,
        max_response_length=900,
        require_source_citation=True,
        terminology_level="technical",
        emphasis=["parametri tecnici", "vincoli di prodotto", "compatibilità", "limitazioni note"],
        omit=["pricing", "considerazioni commerciali"],
    ),
    "sales": RoleConfig(
        name="Sales",
        system_prompt=SALES_SYSTEM_PROMPT,
        max_response_length=400,
        require_source_citation=False,
        terminology_level="client",
        emphasis=["impatto operativo", "casi d'uso", "benefici", "referenze simili"],
        omit=["dettagli implementativi", "vincoli normativi granulari", "pricing"],
    ),
    "bid_manager": RoleConfig(
        name="Bid Manager",
        system_prompt=BID_MANAGER_SYSTEM_PROMPT,
        max_response_length=1100,
        require_source_citation=True,
        terminology_level="legal",
        emphasis=["riferimenti normativi", "adempimenti obbligatori", "rischi di non conformità"],
        omit=["considerazioni commerciali", "dettagli tecnici non di gara"],
    ),
}


# ── output formatting helpers (pure functions) ──────────────────────────────────────

def _doc_label(name: str, decreto, data, pages: List) -> str:
    """Render one DOCUMENT into a compact label, listing all its pages once."""
    parts = [name]
    if pages:
        try:
            ordered = sorted(pages, key=lambda p: int(p))
        except (TypeError, ValueError):
            ordered = list(pages)
        parts.append(("pag. " if len(ordered) == 1 else "pagg. ")
                     + ", ".join(str(p) for p in ordered))
    if decreto:
        parts.append(f"decreto {decreto}")
    if data:
        parts.append(f"del {data}")
    return " · ".join(parts)


def _unique_labels(sources: List[dict]) -> List[str]:
    """One label per DOCUMENT: chunks from the same file (different pages) are merged so the
    document name appears once and its pages are listed together (es. 'X.pdf · pagg. 5, 11, 18')."""
    order: List[tuple] = []
    groups: Dict[tuple, dict] = {}
    for s in sources:
        name = str(s.get("source") or s.get("file") or s.get("id") or "fonte")
        decreto, data = s.get("decreto"), s.get("data_decreto")
        key = (name, str(decreto or ""), str(data or ""))
        if key not in groups:
            groups[key] = {"name": name, "decreto": decreto, "data": data, "pages": []}
            order.append(key)
        page = s.get("page")
        if page is not None and page not in groups[key]["pages"]:
            groups[key]["pages"].append(page)
    return [_doc_label(g["name"], g["decreto"], g["data"], g["pages"])
            for g in (groups[k] for k in order)]


def _red_message(role: RoleConfig, sources: List[dict]) -> str:
    """Explicit, role-phrased refusal (no source → never invent)."""
    if role.terminology_level == "client":
        return ("Su questo punto non ho ancora una conferma verificata: preferisco non azzardare. "
                "Lo faccio verificare al referente tecnico di Engine e le ricontatto.")
    if role.terminology_level == "technical":
        return ("Dato non disponibile nella documentazione tecnica attuale. "
                "Da verificare con il Product Specialist o la scheda di prodotto aggiornata.")
    # legal / bid manager
    msg = ("⚠ Nessun riferimento normativo a supporto: non è possibile dichiarare la conformità. "
           "Rischio di non conformità — escalation all'Ufficio Gare / consulenza legale prima di procedere.")
    labels = _unique_labels(sources)
    if labels:
        msg += "\nRiferimenti potenzialmente rilevanti (da verificare manualmente): " + "; ".join(labels)
    return msg


def _yellow_prefix(role: RoleConfig) -> str:
    if role.terminology_level == "technical":
        return "⚠ Inferito da più fonti.\n"
    if role.terminology_level == "legal":
        return "⚠ Claim derivato da più atti, da verificare.\n"
    return ""  # sales: stays in client language (the prose already hedges)


# ── RoleManager ──────────────────────────────────────────────────────────────────────

class RoleManager:
    """Holds the active role, persists it, and formats answers for that role.

    Standalone: no coupling to the rest of NextPulse. The pipeline imports *this*,
    never the reverse.
    """

    def __init__(self, state_path: "Path | str | None" = STATE_FILE) -> None:
        # state_path=None → in-memory only (no persistence), useful for per-request use.
        self._state_path: "Path | None" = Path(state_path) if state_path else None
        self._role_key: str = self._load()

    # persistence ----------------------------------------------------------------
    def _load(self) -> str:
        if self._state_path and self._state_path.exists():
            try:
                key = json.loads(self._state_path.read_text(encoding="utf-8")).get("role")
                if key in ROLES:
                    return key
            except Exception:
                pass
        return DEFAULT_ROLE

    def _save(self) -> None:
        if self._state_path:
            self._state_path.write_text(
                json.dumps({"role": self._role_key}, ensure_ascii=False), encoding="utf-8"
            )

    # public API ------------------------------------------------------------------
    def set_role(self, role_key: str, persist: bool = True) -> None:
        if role_key not in ROLES:
            raise ValueError(f"Ruolo sconosciuto: {role_key!r}. Validi: {', '.join(ROLES)}")
        self._role_key = role_key
        if persist:
            self._save()

    @property
    def current_key(self) -> str:
        return self._role_key

    def get_current_role(self) -> RoleConfig:
        return ROLES[self._role_key]

    def get_system_prompt(self) -> str:
        return self.get_current_role().system_prompt

    def format_response(
        self, raw_answer: str, sources: List[dict], confidence: Confidence
    ) -> str:
        """Adapt the final answer to the active role + confidence (🟢/🟡/🔴)."""
        role = self.get_current_role()
        conf = confidence.lower()

        if conf == "red":
            return _red_message(role, sources)

        body = raw_answer.strip()
        prefix = _yellow_prefix(role) if conf == "yellow" else ""
        blocks = [prefix + body if prefix else body]

        # No in-text "Fonte:" footer: citations are inline numeric markers ([1], [2]) produced
        # by the model, and the full sources are shown once in the numbered 'Fonti citate'
        # legend (rag_chain._format_sources → UI). Avoids the verbose, repeated footer.
        if conf == "yellow" and role.terminology_level == "client":
            blocks.append("_(informazione indicativa, in conferma)_")

        return "\n\n".join(b for b in blocks if b)


# ── CLI ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="NextPulse — gestione ruolo attivo")
    parser.add_argument("--role", choices=list(ROLES), help="imposta e persiste il ruolo attivo")
    parser.add_argument("--show", action="store_true", help="mostra il ruolo attivo e termina")
    args = parser.parse_args()

    rm = RoleManager()
    if args.role:
        rm.set_role(args.role)  # persisted before any other operation
        print(f"✅ Ruolo attivo impostato: {rm.get_current_role().name} ({rm.current_key})")
    elif args.show:
        r = rm.get_current_role()
        print(f"Ruolo attivo: {r.name} ({rm.current_key}) · citazioni obbligatorie: {r.require_source_citation}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
