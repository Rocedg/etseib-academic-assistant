"""Grafo determinista: diagnosticador -> coordinador (+FAQ) -> RAG -> agent -> supervisor -> output.
Cada paso queda en la traza (trazabilidad y depuración)."""
import time
from datetime import date

from . import agents as A


def handle_query(query: str, indice, faq: list[dict], today: date, profile: str | None = None) -> dict:
    traza, t0 = {"query": query, "today": today.isoformat()}, time.time()

    diag = A.diagnose(query, profile)
    diag["profile"] = diag["profile"] or profile
    traza["diagnosis"] = diag

    route = A.coordinate(diag)
    traza["route"] = route
    fragments = []
    if route == "clarify":
        output = {"action": "clarify", "question": f"Para responder necesito: {', '.join(diag['missing_data'])}."}
    elif route == "escalate":
        output = {"action": "escalate", "create_ticket": A.create_ticket(query, diag, [], ["petición de atención humana"])}
    else:
        faq = A.find_faq(query, faq)
        fragments = indice.search(faq["question"] if faq else query, today, domain=diag["domain"], profile=diag["profile"])
        traza["faq"] = faq["question"] if faq else None
        traza["retrieved"] = [(f.id, round(s, 3)) for f, s in fragments]
        resp = A.answer(route, query, fragments, diag, today) if fragments else {"answer": "", "citations": [],
                                                                         "next_actions": [], "warnings": []}
        decision, reasons = A.supervise(resp, fragments, today, indice.threshold)
        traza["supervisor"] = {"decision": decision, "reasons": reasons}
        if decision == "escalate":
            output = {"action": "escalate", "create_ticket": A.create_ticket(query, diag, fragments, reasons)}
        else:
            resp["warnings"] += reasons
            resp["sources"] = [f"[{n}] {f.source} · {f.section} · {f.url} · v{f.version}"
                               for n, (f, _) in enumerate(fragments, 1) if n in resp["citations"]]
            output = {"action": "answer", **resp}

    traza["output"] = output
    traza["latency_s"] = round(time.time() - t0, 2)
    return traza
