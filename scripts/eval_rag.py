#!/usr/bin/env python
"""Mini evaluation harness for the RAG pipeline (governance KPIs).

Runs a labeled set of questions and measures the behaviours that matter for the
challenge's governance criteria (see docs/KPI.md):

  • grounding accuracy   — in-domain questions answered from the KB (grounded=True)
                           AND out-of-domain questions correctly refused (grounded=False)
  • citation rate        — share of grounded answers carrying at least one inline [n] marker
  • fallback correctness — out-of-domain questions that hit the anti-hallucination fallback
  • latency              — average end-to-end latency per query (ms)

Cases come from a JSON file ({"in_domain": [...], "out_of_domain": [...]}); defaults to
scripts/eval_cases.json. Needs an indexed KB and a working LLM key (it makes real calls).

Usage:
    python scripts/eval_rag.py
    python scripts/eval_rag.py --role bid_manager --cases scripts/eval_cases.json
    python scripts/eval_rag.py --json            # machine-readable summary on stdout
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' / 'role_manager' import standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nextpulse.rag_chain import RAGChain  # noqa: E402

_CITATION = re.compile(r"\[\d+\]")
_DEFAULT_CASES = Path(__file__).resolve().parent / "eval_cases.json"


def _load_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "in_domain": list(data.get("in_domain", [])),
        "out_of_domain": list(data.get("out_of_domain", [])),
    }


def evaluate(chain: RAGChain, cases: dict, role: str | None = None) -> dict:
    rows = []
    for question in cases["in_domain"]:
        r = chain.query(question, role=role)
        rows.append({
            "question": question, "expected": "grounded",
            "grounded": r["grounded"], "cited": bool(_CITATION.search(r["response"])),
            "ok": r["grounded"] is True,
            "latency_ms": r.get("latency_ms") or 0,
        })
    for question in cases["out_of_domain"]:
        r = chain.query(question, role=role)
        rows.append({
            "question": question, "expected": "fallback",
            "grounded": r["grounded"], "cited": bool(_CITATION.search(r["response"])),
            "ok": r["grounded"] is False,
            "latency_ms": r.get("latency_ms") or 0,
        })

    total = len(rows) or 1
    grounded_rows = [r for r in rows if r["expected"] == "grounded"]
    fallback_rows = [r for r in rows if r["expected"] == "fallback"]
    answered = [r for r in grounded_rows if r["grounded"]]
    summary = {
        "n_cases": len(rows),
        "grounding_accuracy": sum(r["ok"] for r in rows) / total,
        "citation_rate": (sum(r["cited"] for r in answered) / len(answered)) if answered else 0.0,
        "fallback_correctness": (
            sum(r["ok"] for r in fallback_rows) / len(fallback_rows)
        ) if fallback_rows else 0.0,
        "avg_latency_ms": sum(r["latency_ms"] for r in rows) / total,
    }
    return {"summary": summary, "rows": rows}


def _print_report(result: dict, role: str | None) -> None:
    s = result["summary"]
    print(f"\n=== NextPulse RAG eval{f' · ruolo {role}' if role else ''} ===")
    print(f"casi totali............ {s['n_cases']}")
    print(f"grounding accuracy..... {s['grounding_accuracy']:.0%}")
    print(f"citation rate.......... {s['citation_rate']:.0%}  (risposte grounded con [n])")
    print(f"fallback correctness... {s['fallback_correctness']:.0%}  (fuori-dominio rifiutati)")
    print(f"latenza media.......... {s['avg_latency_ms']:.0f} ms")
    print("\n  dettaglio:")
    for r in result["rows"]:
        mark = "✓" if r["ok"] else "✗"
        cite = "[n]" if r["cited"] else "   "
        print(f"   {mark} {cite} [{r['expected']:>8}] {r['question'][:60]}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="NextPulse — eval harness RAG (KPI governance)")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES,
                        help="JSON con {in_domain:[...], out_of_domain:[...]}")
    parser.add_argument("--role", choices=["sales", "presales", "bid_manager"], default=None)
    parser.add_argument("--json", action="store_true", help="stampa il summary in JSON")
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    result = evaluate(RAGChain(), cases, role=args.role)

    if args.json:
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    else:
        _print_report(result, args.role)


if __name__ == "__main__":
    main()
