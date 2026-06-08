"""Tests for the eval harness (scripts/eval_rag.py) — metrics logic, no LLM."""


class _FakeChain:
    """Returns grounded+cited for in-domain questions, a fallback for out-of-domain."""
    def query(self, question, role=None):
        if "carbonara" in question or "calcio" in question:
            return {"grounded": False, "response": "Non presente nella documentazione.",
                    "latency_ms": 10}
        return {"grounded": True, "response": "Risposta con citazione [1].", "latency_ms": 20}


def test_evaluate_computes_metrics():
    from scripts.eval_rag import evaluate
    cases = {
        "in_domain": ["Omologazione autovelox?", "Gestione ZTL?"],
        "out_of_domain": ["Ricetta della carbonara?", "Chi ha vinto il calcio?"],
    }
    result = evaluate(_FakeChain(), cases)
    s = result["summary"]
    assert s["n_cases"] == 4
    assert s["grounding_accuracy"] == 1.0      # 2 grounded + 2 correctly refused
    assert s["citation_rate"] == 1.0           # both grounded answers carry [1]
    assert s["fallback_correctness"] == 1.0    # both out-of-domain refused
    assert s["avg_latency_ms"] == 15.0


def test_evaluate_flags_missing_citation_and_wrong_grounding():
    from scripts.eval_rag import evaluate

    class _Weak:
        def query(self, question, role=None):
            # grounded but WITHOUT an inline marker, and never refuses
            return {"grounded": True, "response": "Risposta senza marcatore.", "latency_ms": 5}

    result = evaluate(_Weak(), {"in_domain": ["q1"], "out_of_domain": ["fuori"]})
    s = result["summary"]
    assert s["citation_rate"] == 0.0           # no [n] in the grounded answer
    assert s["fallback_correctness"] == 0.0    # out-of-domain wrongly answered as grounded
    assert s["grounding_accuracy"] == 0.5      # 1 of 2 correct


def test_script_imports():
    import scripts.eval_rag  # noqa: F401
