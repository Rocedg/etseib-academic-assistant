# Architecture

This prototype is a deterministic agent graph:

1. The diagnostician classifies the Spanish student query.
2. Rule-based coordination selects enrollment, Learning Agreement, or document search.
3. The hybrid retriever applies validity and profile filters, then uses BM25 (and optional Ollama embeddings).
4. The specialist agent answers only from numbered fragments and must cite them.
5. The supervisor checks citations, validity, retrieval confidence, and unsupported claims.
6. Unsupported or human-support cases become an escalation ticket.

The corpus and question bank are intentionally fictional toy data. This is not the final proof of concept.

## Commands

```bash
python main.py "¿Quién firma el Learning Agreement?" --profile movilidad_saliente --today 2026-10-01
python evaluate.py --today 2026-10-01
```

The default backend is `mock`, which validates the flow without installing dependencies. Ollama can be enabled with `LLM_BACKEND=ollama`, `LLM_MODEL`, and `EMBEDDING_MODEL`.

## Modules

| Module | Responsibility |
|---|---|
| `assistant/llm.py` | Provider-independent JSON calls and deterministic mock |
| `assistant/corpus.py` | Markdown ingestion and validity metadata |
| `assistant/retrieval.py` | BM25 and optional embedding retrieval |
| `assistant/agents.py` | Diagnosis, specialist response, deadlines, and supervision |
| `assistant/pipeline.py` | Fixed orchestration graph and trace generation |
