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
    max_response_length: int                 # approximate token cap for the answer
    require_source_citation: bool            # True for Pre-Sales and Bid Manager
    terminology_level: TerminologyLevel
    emphasis: List[str] = field(default_factory=list)
    omit: List[str] = field(default_factory=list)


# ── Role registry ──────────────────────────────────────────────────────────────────

ROLES: Dict[str, RoleConfig] = {
    "sales": RoleConfig(
        name="Sales",
        system_prompt=SALES_SYSTEM_PROMPT,
        max_response_length=140,
        require_source_citation=False,
        terminology_level="client",
        emphasis=["impatto operativo", "casi d'uso", "benefici", "referenze simili"],
        omit=["dettagli implementativi", "vincoli normativi granulari", "pricing"],
    ),
    "presales": RoleConfig(
        name="Pre-Sales",
        system_prompt=PRESALES_SYSTEM_PROMPT,
        max_response_length=380,
        require_source_citation=True,
        terminology_level="technical",
        emphasis=["parametri tecnici", "vincoli di prodotto", "compatibilità", "limitazioni note"],
        omit=["pricing", "considerazioni commerciali"],
    ),
    "bid_manager": RoleConfig(
        name="Bid Manager",
        system_prompt=BID_MANAGER_SYSTEM_PROMPT,
        max_response_length=460,
        require_source_citation=True,
        terminology_level="legal",
        emphasis=["riferimenti normativi", "adempimenti obbligatori", "rischi di non conformità"],
        omit=["considerazioni commerciali", "dettagli tecnici non di gara"],
    ),
}


# ── output formatting helpers (pure functions) ──────────────────────────────────────

def _source_label(s: dict) -> str:
    """Render a retrieval source dict into a compact, human-readable label."""
    parts = [str(s.get("source") or s.get("file") or s.get("id") or "fonte")]
    if s.get("page"):
        parts.append(f"pag. {s['page']}")
    if s.get("decreto"):
        parts.append(f"decreto {s['decreto']}")
    if s.get("data_decreto"):
        parts.append(f"del {s['data_decreto']}")
    return " · ".join(parts)


def _unique_labels(sources: List[dict]) -> List[str]:
    seen, out = set(), []
    for s in sources:
        label = _source_label(s)
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


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


def _sources_footer(role: RoleConfig, sources: List[dict]) -> str:
    labels = _unique_labels(sources)
    if not labels:
        return ""
    if role.terminology_level == "legal":
        return "Fonte verificabile: " + "; ".join(labels)
    return "Fonte: " + "; ".join(labels)


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

        if role.require_source_citation:
            footer = _sources_footer(role, sources)
            if footer:
                blocks.append(footer)
        # Sales (require_source_citation=False): keep client-facing prose, no source block.
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
