# Remote server and web UI

Runs the assistant on one always-on laptop (Ollama + the pipeline in `assistant/`) and lets anyone use it from a browser on any computer, without installing anything there.

```
any browser ──HTTPS──▶ Tailscale Funnel ──▶ server.py 127.0.0.1:8080 ──▶ pipeline ──▶ Ollama 127.0.0.1:11434
```

- **Ollama is never exposed.** It listens only on `127.0.0.1`; the gateway runs the whole RAG pipeline locally and only answers `/v1/queries`.
- **Personal tokens.** Every call except `/` and `/health` needs `Authorization: Bearer <token>`. Only SHA-256 hashes are stored; revoking needs no restart.
- **Job queue.** `POST /v1/queries` returns an id immediately; the UI polls it. A query takes 90-105 s on CPU, so the queue keeps one worker and shows the position. Closing the tab does not lose the answer.
- **Web UI** (`ui.html`, served at `/`): the four stages live (diagnosis, retrieval, drafting, verification), the answer with clickable `[n]` citations, the retrieved fragments with the deadline tool's annotations, the supervisor decision, per-call LLM statistics and a history that survives restarts.

## Server laptop (once)

Install Git, Python 3.12, Ollama and Tailscale, clone this repository and run from its root:

```powershell
powershell -ExecutionPolicy Bypass -File server\install_server.ps1
```

The script sets `OLLAMA_HOST=127.0.0.1:11434`, pulls `qwen3:8b` and `qwen3-embedding:0.6b`, creates your token, registers the scheduled task `ETSEIB-assistant-server` (starts at log-on, restarts on failure) and publishes the gateway with `tailscale funnel --bg 8080`. Re-running it is safe.

Tokens for the team: `py -3 server\server.py token new ana` · `token list` · `token revoke ana`.

## Using it

Open the `https://<laptop>.<tailnet>.ts.net/` URL in any browser and paste your token. Nothing else is needed on the client.

## Rehearsal without a model

```bash
python server/server.py --mock      # http://127.0.0.1:8080/ - fake answers, no token, nothing written to disk
```

## API

| Method and path | Token | What it does |
|---|---|---|
| `GET /` | No | Web UI (holds no data; asks for the token) |
| `GET /health` | No | Backend, model, Ollama version, fragment count, queue length |
| `GET /v1/queries` | Yes | Latest queries (id, text, status, latency) |
| `POST /v1/queries` | Yes | Body `{"query": "...", "profile": "grado", "today": "2026-10-01"}`; returns `202` with `id`. `?wait=1` waits for the result |
| `GET /v1/queries/{id}` | Yes | `status` (`queued`, `running`, `done`, `error`), `ahead`, `stage`, `stages`, `result`, `retrieved`, `detail`, `llm_calls`, `latency_s` |

Full traces are written to `%USERPROFILE%\.etseib-assistant\traces\<id>.json`. Never commit tokens or put them in URLs or screenshots.

## Limits

- After a Windows restart the gateway only comes back when someone logs in: pause Windows Update while the laptop is unattended.
- The index is rebuilt at start-up (seconds with the toy corpus, about a minute with ~20 documents on CPU).
