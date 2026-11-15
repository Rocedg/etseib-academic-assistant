"""Remote gateway for the assistant: runs the pipeline on the server laptop and serves the web UI.

    Any browser ──HTTPS──▶ Tailscale Funnel ──▶ 127.0.0.1:8080 (this gateway) ──▶ pipeline ──▶ Ollama 127.0.0.1:11434

Only the standard library is used (Python 3.10+). Ollama is never exposed: the gateway runs the whole
RAG pipeline locally and only answers /v1/queries. Everything except / and /health needs a personal token:

    python server/server.py token new edgar      # creates one and prints it ONCE
    python server/server.py token list
    python server/server.py token revoke edgar   # takes effect immediately, no restart

Run:
    python server/server.py                      # real mode: Ollama + qwen3:8b by default
    python server/server.py --mock               # rehearsal on any computer: no model, no token, fake answers

Environment (optional): LLM_MODEL (qwen3:8b), EMBEDDING_MODEL (qwen3-embedding:0.6b), SERVER_PORT (8080),
OLLAMA_URL (http://127.0.0.1:11434), SERVER_DATA (~/.etseib-assistant), MOCK_STAGE_DELAY (1.5 s in --mock).
"""
import hashlib
import hmac
import json
import os
import queue
import secrets
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MOCK = "--mock" in sys.argv
if MOCK:
    os.environ["LLM_BACKEND"] = "mock"
os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("LLM_MODEL", "qwen3:8b")
os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")   # read by assistant/llm.py at import time

PORT = int(os.getenv("SERVER_PORT", "8080"))
OLLAMA = os.environ["OLLAMA_URL"]
DATA_DIR = Path(os.getenv("SERVER_DATA", Path.home() / ".etseib-assistant"))
TOKENS = DATA_DIR / "tokens.json"       # only the SHA-256 of each token is stored
ACCESS_LOG = DATA_DIR / "access.jsonl"  # who called what, status and duration (never the query text)
TRACES = DATA_DIR / "traces"            # full trace of every query (debugging and evaluation)
UI = HERE / "ui.html"
MAX_QUEUE = int(os.getenv("SERVER_MAX_QUEUE", "20"))
HISTORY = int(os.getenv("SERVER_HISTORY", "30"))    # recent queries reloaded at start-up
MAX_BODY = 64_000
STAGE_DELAY = float(os.getenv("MOCK_STAGE_DELAY", "1.5")) if MOCK else 0.0
AUTH = not MOCK                         # --mock only listens on 127.0.0.1 and returns fake answers


# ───────────────────────── tokens ─────────────────────────
def _hash(t): return hashlib.sha256(t.encode()).hexdigest()


def _read_tokens():
    return json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}


def token_command(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tk = _read_tokens()
    if args[:1] == ["new"] and len(args) == 2:
        t = secrets.token_urlsafe(32)
        tk[args[1]] = {"sha256": _hash(t), "created": datetime.now().isoformat(timespec="seconds")}
        TOKENS.write_text(json.dumps(tk, indent=2), encoding="utf-8")
        print(f"Token para {args[1]} (cópialo ahora; no se vuelve a mostrar):\n\n{t}\n")
    elif args[:1] == ["revoke"] and len(args) == 2:
        print("Revocado." if tk.pop(args[1], None) else "No existe.")
        TOKENS.write_text(json.dumps(tk, indent=2), encoding="utf-8")
    elif args[:1] == ["list"]:
        for n, v in tk.items():
            print(f"{n:15} creado {v['created']}")
    else:
        print("Uso: python server/server.py token new|revoke <nombre>  ·  python server/server.py token list")


def who(header):
    """Owner of the token, or None. Constant-time comparison; the file is re-read so revoking needs no restart."""
    if not AUTH:
        return "ensayo"
    if not header or not header.startswith("Bearer "):
        return None
    h = _hash(header[7:].strip())
    for name, v in _read_tokens().items():
        if hmac.compare_digest(h, v["sha256"]):
            return name
    return None


# ───────────────────────── pipeline (single worker queue) ─────────────────────────
JOBS, QUEUE = {}, queue.Queue()
PIPE = {}
CURRENT = {"job": None}   # one worker, so there is at most one running job


def _mark(stage):
    j = JOBS.get(CURRENT["job"])
    if j is not None:
        j["stage"] = stage
        j["stages"].append([stage, round(time.time() - j["started"], 2)])
    if STAGE_DELAY:
        time.sleep(STAGE_DELAY)   # rehearsal only: makes the stages visible in the UI


def load_pipeline():
    sys.path.insert(0, str(ROOT))
    import assistant.agents as A
    import assistant.llm as L
    from assistant.corpus import load_corpus
    from assistant.pipeline import handle_query
    from assistant.retrieval import Index
    t = time.time()
    index = Index(load_corpus(ROOT / "data" / "corpus"))   # embeddings computed ONCE, at start-up
    PIPE.update(index=index, llm=L, handle=handle_query,
                faq=json.loads((ROOT / "data" / "faq.json").read_text(encoding="utf-8")))

    # Instrumentation without touching assistant/: pipeline.py calls A.<function>, so wrapping the
    # module attributes is enough for the UI to see the current stage and the retrieved fragments.
    def wrap(fn, stage):
        def f(*a, **k):
            _mark(stage)
            return fn(*a, **k)
        return f
    A.diagnose = wrap(A.diagnose, "diagnosis")
    A.answer = wrap(A.answer, "drafting")
    A.supervise = wrap(A.supervise, "verification")
    search = index.search

    def search_and_record(*a, **k):
        _mark("retrieval")
        res = search(*a, **k)
        j = JOBS.get(CURRENT["job"])
        if j is not None:
            today = k.get("today") or (a[1] if len(a) > 1 else date.today())
            # text as the agent sees it: each deadline annotated by the deterministic deadline tool
            j["retrieved"] = [{"n": n, "id": f.id, "source": f.source, "section": f.section, "url": f.url,
                               "version": f.version, "score": round(s, 4),
                               "text": A.annotate_deadlines(f.text, today)[:1500]}
                              for n, (f, s) in enumerate(res, 1)]
        return res
    index.search = search_and_record
    print(f"Pipeline cargado en {time.time() - t:.1f} s ({len(index.fragments)} fragmentos, "
          f"backend {L.BACKEND}, modelo {L.MODEL})")


def worker():
    L = PIPE["llm"]
    while True:
        jid = QUEUE.get()
        j = JOBS[jid]
        j.update(status="running", started=time.time(), stage="start", stages=[], retrieved=[])
        CURRENT["job"] = jid
        n0 = len(L.STATISTICS)
        try:
            inp = j["input"]
            today = date.fromisoformat(inp.get("today") or date.today().isoformat())
            trace = PIPE["handle"](inp["query"], PIPE["index"], PIPE["faq"], today, inp.get("profile"))
            j.update(status="done", result=trace["output"], latency_s=trace["latency_s"],
                     detail={k: trace.get(k) for k in ("diagnosis", "route", "faq", "supervisor")})
        except Exception as e:  # returned to the client; the stack trace stays in the console
            traceback.print_exc()
            trace = {"error": f"{type(e).__name__}: {e}"}
            j.update(status="error", error=trace["error"])
        j["llm_calls"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()}
                          for s in L.STATISTICS[n0:]]
        j["finished"] = time.time()
        j["stage"] = j["status"]
        CURRENT["job"] = None
        try:
            if MOCK:
                raise StopIteration   # rehearsal: nothing is written to disk
            TRACES.mkdir(parents=True, exist_ok=True)
            trace["_server"] = {k: v for k, v in j.items() if k != "event"}
            (TRACES / f"{jid}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2, default=str),
                                                encoding="utf-8")
        except StopIteration:
            pass
        except Exception:
            traceback.print_exc()
        j["event"].set()


def load_history():
    """Reload the latest finished queries so the UI history survives a restart."""
    if MOCK or not TRACES.exists():
        return
    for p in sorted(TRACES.glob("*.json"), key=lambda p: p.stat().st_mtime)[-HISTORY:]:
        try:
            r = json.loads(p.read_text(encoding="utf-8")).get("_server")
            if r and r.get("status") in ("done", "error"):
                ev = threading.Event()
                ev.set()
                JOBS[r["id"]] = {**r, "event": ev}
        except Exception:
            pass


def view(jid):
    j = JOBS[jid]
    v = {k: j[k] for k in ("id", "status", "created", "result", "error", "latency_s", "stage", "stages",
                           "retrieved", "detail", "llm_calls", "user") if k in j}
    v.update(query=j["input"].get("query"), profile=j["input"].get("profile"), today=j["input"].get("today"))
    if j["status"] == "running":
        v["elapsed_s"] = round(time.time() - j["started"], 1)
    if j["status"] == "queued":
        v["ahead"] = sum(1 for t in JOBS.values() if t["status"] in ("queued", "running") and t["created"] < j["created"])
    return v


# ───────────────────────── HTTP ─────────────────────────
class Gateway(BaseHTTPRequestHandler):
    server_version = "ETSEIBAssistant/2"

    def log_message(self, *a):  # own log in access.jsonl
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        self._code = code

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("cuerpo demasiado grande")
        return self.rfile.read(n) if n else b""

    def _handle(self, method):
        t0, self._code, user = time.time(), 0, None
        path, _, qs = self.path.partition("?")
        try:
            if path in ("/", "/ui") and method == "GET":   # the page holds no data; it asks for the token
                return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if path == "/health" and method == "GET":
                L = PIPE.get("llm")
                return self._send(200, {"ok": bool(PIPE), "auth": AUTH, "backend": getattr(L, "BACKEND", None),
                                        "model": getattr(L, "MODEL", None), "ollama": None if MOCK else _ollama_version(),
                                        "fragments": len(PIPE["index"].fragments) if PIPE else 0,
                                        "queued": QUEUE.qsize()})
            user = who(self.headers.get("Authorization"))
            if not user:
                return self._send(401, {"error": "falta un token válido (Authorization: Bearer ...)"})
            if path == "/v1/queries" and method == "GET":
                last = sorted(JOBS.values(), key=lambda t: t["created"], reverse=True)[:HISTORY]
                return self._send(200, [{"id": t["id"], "status": t["status"], "created": t["created"],
                                         "latency_s": t.get("latency_s"), "query": t["input"].get("query")}
                                        for t in last])
            if path == "/v1/queries" and method == "POST":
                return self._new_query(user, "wait=1" in qs)
            if path.startswith("/v1/queries/") and method == "GET":
                jid = path.rsplit("/", 1)[1]
                return self._send(200, view(jid)) if jid in JOBS else self._send(404, {"error": "no existe"})
            return self._send(404, {"error": "ruta no permitida"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            _log({"ts": datetime.now().isoformat(timespec="seconds"), "user": user,
                  "ip": self.headers.get("X-Forwarded-For") or self.client_address[0],
                  "method": method, "path": path, "code": self._code, "ms": round((time.time() - t0) * 1000)})

    def _new_query(self, user, wait):
        try:
            e = json.loads(self._body() or b"{}")
        except json.JSONDecodeError:
            raise ValueError("el cuerpo no es JSON")
        if not isinstance(e.get("query"), str) or not e["query"].strip() or len(e["query"]) > 2000:
            raise ValueError("falta 'query' (texto de 1 a 2000 caracteres)")
        if e.get("today"):
            date.fromisoformat(e["today"])   # ValueError -> 400
        if QUEUE.qsize() >= MAX_QUEUE:
            return self._send(429, {"error": f"cola llena ({MAX_QUEUE}); prueba más tarde"})
        jid = uuid.uuid4().hex[:12]
        JOBS[jid] = {"id": jid, "status": "queued", "created": time.time(), "user": user,
                     "input": {k: e.get(k) for k in ("query", "profile", "today")}, "event": threading.Event()}
        QUEUE.put(jid)
        if wait:
            JOBS[jid]["event"].wait()
            return self._send(200, view(jid))
        return self._send(202, view(jid))

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")


def _ollama_version():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/version", timeout=3) as r:
            return json.loads(r.read()).get("version")
    except Exception as e:
        return f"sin respuesta ({e.__class__.__name__})"


_LOG_LOCK = threading.Lock()


def _log(row):
    if MOCK:
        return
    with _LOG_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with ACCESS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    if sys.argv[1:2] == ["token"]:
        return token_command(sys.argv[2:])
    if AUTH and not _read_tokens():
        sys.exit("No hay tokens. Crea uno antes:  python server/server.py token new <tu_nombre>")
    load_pipeline()
    load_history()
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Gateway)   # never 0.0.0.0: outside access only via the tunnel
    print(f"Interfaz: http://127.0.0.1:{PORT}/" + ("   [MODO ENSAYO: respuestas falsas, sin token]" if MOCK else
          f"   ·  Ollama {_ollama_version()}  ·  datos en {DATA_DIR}"))
    srv.serve_forever()


if __name__ == "__main__":
    main()
