"""Run the question bank and calculate component and global metrics.
The mock backend validates the flow, not answer quality."""
import argparse
import json
import statistics
from datetime import date
from pathlib import Path

from assistant import llm
from assistant.corpus import load_corpus
from assistant.pipeline import handle_query
from assistant.retrieval import Index

DATA = Path(__file__).parent / "data"


def main(today: date):
    indice = Index(load_corpus(DATA / "corpus"))
    faq = json.loads((DATA / "faq.json").read_text(encoding="utf-8"))
    banco = [json.loads(l) for l in (DATA / "question_bank.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    filas, errores = [], []
    for c in banco:
        print(f"[{c['id']}] {c['query']}", flush=True)
        try:
            t = handle_query(c["query"], indice, faq, today, c.get("profile"))
        except Exception as e:  # un fallo (timeout, JSON inválido) no detiene el banco
            errores.append((c["id"], repr(e)[:300]))
            print("   ERROR:", errores[-1][1], flush=True)
            continue
        s = t["output"]
        retrieved = [i for i, _ in t.get("retrieved", [])]
        text = json.dumps(s, ensure_ascii=False)
        filas.append({
            "id": c["id"],             "action": s["action"], "action_ok": s["action"] == c["expected_action"],
            "recall@k": (c["expected_source"] in retrieved) if "expected_source" in c else None,
            "key_facts_ok": all(h in text for h in c["key_facts"]) if c.get("key_facts") and s["action"] == "answer" else None,
            "with_source": bool(s.get("sources")) if s["action"] == "answer" else None,
            "unit_ok": (s["create_ticket"]["recommended_unit"] == c["expected_unit"]) if "expected_unit" in c and s["action"] == "escalate" else None,
            "latency_s": t["latency_s"]})
        (DATA.parent / "traces").mkdir(exist_ok=True)
        (DATA.parent / "traces" / f"{c['id']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if not filas:
        print("\nNinguna query terminó. Errores:", errores)
        return
    pct = lambda k: (lambda v: f"{100 * sum(v) / len(v):.0f} % (n={len(v)})" if v else "n/a")([f[k] for f in filas if f[k] is not None])
    for f in filas:
        print(f)
    print("\nCorrect action (answer/clarify/escalate):", pct("action_ok"))
    print("Retrieval recall@k:", pct("recall@k"))
    print("Correct key facts:", pct("key_facts_ok"))
    print("Answers with sources:", pct("with_source"))
    print("Correct escalation unit:", pct("unit_ok"))
    lat = sorted(f["latency_s"] for f in filas)
    print("Latencia p95 (s):", lat[min(len(lat) - 1, int(0.95 * len(lat)))], "· media:", round(statistics.mean(lat), 2))
    print("Consultas con error:", f"{len(errores)} de {len(banco)}", errores or "")
    # Velocidad real por task (solo con Ollama): separa la lectura del prompt de la generación.
    for task in sorted({e["task"] for e in llm.STATISTICS}):
        es = [e for e in llm.STATISTICS if e["task"] == task]
        tps = lambda tok, s: round(sum(e[tok] for e in es) / max(sum(e[s] for e in es), 1e-9), 1)
        print(f"Ollama · {task} ({es[0]['model']}, {len(es)} llamadas): prompt medio {round(statistics.mean(e['tokens_prompt'] for e in es))} tok"
              f" a {tps('tokens_prompt', 's_prompt')} tok/s · generación {tps('tokens_gen', 's_gen')} tok/s"
              f" · s por llamada {round(statistics.mean(e['s_total'] for e in es), 1)} (carga máx. {round(max(e['s_carga'] for e in es), 1)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default="2026-10-01")
    main(date.fromisoformat(ap.parse_args().today))
