# ETSEIB academic assistant

Prototype of an agentic assistant for ETSEIB academic queries. It uses a toy fictional corpus and question bank to demonstrate retrieval-augmented answers, citations, deterministic deadline handling, supervision, and escalation. It is **not** the final proof of concept.

## Run in mock mode

The mock backend is the default and uses only the Python standard library:

```bash
python main.py "¿Quién firma el Learning Agreement?" --profile movilidad_saliente --today 2026-10-01
python evaluate.py --today 2026-10-01
```

## Run with Ollama

Install Ollama locally, make the required model available, then run:

```bash
set LLM_BACKEND=ollama
set LLM_MODEL=qwen3:8b
set EMBEDDING_MODEL=qwen3-embedding:0.6b
python demo.py
```

On Ubuntu, replace `set` with `export`. The classroom scripts automate the CPU setup and are documented in [docs/classroom-test.md](docs/classroom-test.md).

## Layout

- `assistant/`: Python modules for diagnosis, retrieval, agents, and supervision
- `data/`: Spanish toy corpus, FAQ, and question bank
- `main.py`, `evaluate.py`, `demo.py`: CLI entry points
- `scripts/classroom/`: Ubuntu and Windows Ollama test scripts
- `docs/`: architecture, classroom, demo, and remote-access guides

The corpus, FAQ content, profile values such as `movilidad_saliente`, and LLM prompt content remain Spanish because the assistant serves Spanish-speaking students.

## Team workflow

Use one branch per task, open a Pull Request into `main`, and do not push directly to `main`.
