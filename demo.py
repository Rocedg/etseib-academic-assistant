"""Demo en directo del assistant: busca Ollama en la red, recupera documentos y responde en tiempo real.
Uso: python demo.py [--url http://IP:11434] [--today 2026-10-01] [--sin-model]
Una sola llamada al model por question (como la PoC). Las peticiones de atención humana se derivan
por regla y las preguntas sin documentos relacionados se descartan, en ambos casos sin pasar por el model."""
import argparse
import concurrent.futures as cf
import ipaddress
import json
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from assistant import retrieval
from assistant.agents import UNITS, annotate_deadlines
from assistant.corpus import load_corpus
from assistant.retrieval import Index

DATA = Path(__file__).parent / "data"
LAST_URL = Path(__file__).parent / ".ollama_url"     # última dirección que funcionó (acelera el arranque)
PORT = 11434
MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
K = int(os.getenv("RAG_K", "3"))
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "900"))
MAX_TOKENS = int(os.getenv("DEMO_MAX_TOKENS", "300"))   # tope de la answer: en CPU cada token cuenta
THRESHOLD = float(os.getenv("DEMO_UMBRAL", "0.45"))        # similitud mínima del mejor documento; calibrado con el kit

# Regla fija: pedir una persona no necesita model (y debe ser instantáneo).
HUMAN_REQUEST = re.compile(r"hablar con (una |alguna )?(persona|humano|alguien)|atenci[oó]n (humana|presencial)|\bqueja"
                    r"|reclamaci[oó]n|\brecurso\b|parlar amb (una persona|alg[uú])|queixa"
                    r"|(talk|speak) (to|with) (a |some)(person|human|one)|complaint", re.I)

SISTEMA = """Eres el assistant académico de la ETSEIB. Hoy es {today}.
Responde SOLO con la información de los fragments numerados y cita cada dato con [n].
Si los fragments no contienen la answer, dilo en una frase y no inventes nada.
Si un plazo lleva su estado entre corchetes (abierto, cerrado...), úsalo tal cual: no calcules fechas.
Contesta solo lo que se question, en 1 a 3 frases, sin añadir nada que no diga un fragmento.
Responde en el mismo language que la question."""

GRAY, BOLD, GREEN, YELLOW, FIN = "\033[90m", "\033[1m", "\033[32m", "\033[33m", "\033[0m"


# ---------- Red: encontrar Ollama sin saber la IP ----------
def _peticion(url, body=None, timeout=TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def version_ollama(url):
    try:
        return _peticion(f"{url}/api/version", timeout=2).get("version")
    except Exception:
        return None


def _ips_locales():
    ips = []
    try:  # IP de la interfaz con route por defecto (no envía nada)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(ips) if not ip.startswith(("127.", "169.254."))]


def _puerto_abierto(ip):
    try:
        with socket.create_connection((ip, PORT), timeout=0.6):
            return ip
    except OSError:
        return None


def buscar_ollama(url_manual):
    if url_manual:
        return url_manual, version_ollama(url_manual)
    candidatas = [f"http://localhost:{PORT}"]
    if LAST_URL.exists():
        candidatas.insert(0, LAST_URL.read_text(encoding="utf-8").strip())
    for u in candidatas:
        if v := version_ollama(u):
            return u, v
    for ip in _ips_locales():
        red = ipaddress.ip_network(f"{ip}/24", strict=False)
        print(f"  Buscando en la red {red} ...", flush=True)
        with cf.ThreadPoolExecutor(128) as ex:
            abiertos = [h for h in ex.map(_puerto_abierto, (str(h) for h in red.hosts() if str(h) != ip)) if h]
        for h in abiertos:
            u = f"http://{h}:{PORT}"
            if v := version_ollama(u):
                return u, v
    return None, None


# ---------- Modelo ----------
class Ollama:
    def __init__(self, url):
        self.url = url
        self.ultimo_vector = None

    def modelos(self):
        return {m["name"] for m in _peticion(f"{self.url}/api/tags", timeout=10)["models"]}

    def precargar(self):
        """Carga el model y lo deja en memoria toda la sesión (keep_alive -1): sin recargas entre preguntas."""
        _peticion(f"{self.url}/api/chat", {"model": MODEL, "messages": [], "keep_alive": -1})

    def embed(self, textos):
        r = _peticion(f"{self.url}/api/embed", {"model": EMBEDDING_MODEL, "input": textos, "keep_alive": -1})
        self.ultimo_vector = r["embeddings"][0]
        return r["embeddings"]

    def chat_stream(self, system, user, cola, caja):
        """Hilo aparte: el hilo principal sigue atento a Ctrl+C y al contador de espera."""
        body = {"model": MODEL, "stream": True, "keep_alive": -1, "think": False,
                  "options": {"temperature": 0, "num_predict": MAX_TOKENS},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            for intento in (body, {k: v for k, v in body.items() if k != "think"}):
                req = urllib.request.Request(f"{self.url}/api/chat", json.dumps(intento).encode(),
                                             {"Content-Type": "application/json"})
                try:
                    r = urllib.request.urlopen(req, timeout=TIMEOUT)
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 400 or "think" not in intento:  # 400 = model sin modo de razonamiento
                        raise
            with r:
                caja["r"] = r
                for linea in r:
                    if linea.strip():
                        cola.put(("dato", json.loads(linea)))
        except Exception as e:
            cola.put(("error", e))
        cola.put(("fin", None))


# ---------- Una question ----------
def _coseno(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b) or 1)


def escalate(motivo, unidad="SIAE"):
    print(f"\n  {YELLOW}→ Derivo tu query a: {unidad}{FIN} ({motivo}).")
    print(f"  {GRAY}Se crearía un create_ticket con la question y los datos aportados para que te atienda una persona.{FIN}")


def handle_query(query, indice, ollama, today):
    if HUMAN_REQUEST.search(query):
        return escalate("has pedido atención personal; regla fija, sin model", UNITS["documental"])

    t0 = time.time()
    fragments = indice.search(query, today, k=K)
    if indice.vec and ollama:
        sims = [_coseno(ollama.ultimo_vector, indice.vec[indice.fragments.index(f)]) for f, _ in fragments]
    else:
        sims = [None] * len(fragments)
    print(f"\n  {BOLD}Documentos encontrados{FIN} {GRAY}({time.time() - t0:.1f} s){FIN}")
    for n, ((f, _), s) in enumerate(zip(fragments, sims), 1):
        sim = f" · similitud {s:.2f}" if s is not None else ""
        print(f"   [{n}] {f.source} · {f.section}{GRAY}{sim}{FIN}")
    if not fragments or (sims[0] is not None and max(sims) < THRESHOLD):
        print(f"\n  No encuentro nada sobre esto en los documentos que tengo, así que no respondo para no inventar.")
        return escalate("sin documentos relacionados")

    context = "\n\n".join(f"[{n}] ({f.source}, {f.section})\n{annotate_deadlines(f.text, today)}"
                           for n, (f, _) in enumerate(fragments, 1))
    if not ollama:  # plan B sin red: se enseña el mejor fragmento tal cual
        print(f"\n  {BOLD}Fragmento más relevante [1]:{FIN}\n  " + context.split("\n\n")[0].replace("\n", "\n  "))
        return

    cola, caja = queue.Queue(), {}
    threading.Thread(target=ollama.chat_stream, daemon=True,
                     args=(SISTEMA.format(today=today.isoformat()), f"Fragmentos:\n{context}\n\nPregunta: {query}", cola, caja)).start()
    t0, first_token, text, final = time.time(), None, "", {}
    print()
    try:
        while True:
            try:
                tipo, d = cola.get(timeout=0.25)
            except queue.Empty:
                if first_token is None:
                    print(f"\r  {GRAY}El model está leyendo los documentos... {time.time() - t0:.0f} s{FIN}", end="", flush=True)
                continue
            if tipo == "error":
                raise d
            if tipo == "fin":
                break
            if d.get("error"):
                raise RuntimeError(d["error"])
            trozo = d.get("message", {}).get("content", "")
            if first_token is None:
                trozo = trozo.lstrip()
                if trozo:
                    first_token = time.time() - t0
                    print("\r" + " " * 60 + "\r  ", end="")
            if trozo:
                text += trozo
                print(trozo.replace("\n", "\n  "), end="", flush=True)
            if d.get("done"):
                final = d
    except KeyboardInterrupt:
        if caja.get("r"):
            caja["r"].close()  # cortar la conexión detiene la generación en Ollama
        print(f"\n  {YELLOW}[answer interrumpida]{FIN}")
        return

    citations = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(fragments)})
    print()
    if citations:
        print(f"\n  {BOLD}Fuentes{FIN}")
        for n in citations:
            f = fragments[n - 1][0]
            print(f"   [{n}] {f.source} · {f.section} · {f.url}")
    else:
        print(f"\n  {YELLOW}(La answer no cita ninguna source: tómala con cautela.){FIN}")
    gen = final.get("eval_count", 0) / max(final.get("eval_duration", 0) / 1e9, 1e-9)
    print(f"  {GRAY}Empezó a escribir a los {first_token or 0:.0f} s · total {time.time() - t0:.0f} s · "
          f"{final.get('prompt_eval_count', '?')} tokens leídos · {gen:.1f} tokens/s escribiendo{FIN}")


# ---------- Arranque ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("OLLAMA_URL"), help="dirección de Ollama si la búsqueda automática falla")
    ap.add_argument("--today", default=date.today().isoformat(), help="fecha de referencia para los plazos")
    ap.add_argument("--no-model", action="store_true", help="fallback without a network or model")
    a = ap.parse_args()
    today = date.fromisoformat(a.today)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.system("")  # activa los colores ANSI en la consola de Windows

    print(f"{BOLD}Asistente académico ETSEIB · demo{FIN}")
    ollama = None
    if not a.no_model:
        print("Buscando el servidor Ollama...", flush=True)
        url, version = buscar_ollama(a.url)
        if not version:
            print(f"\n{YELLOW}No encuentro Ollama{FIN}{' en ' + a.url if a.url else ' en esta red'}. Comprueba que:\n"
                  "  - los dos ordenadores están en la misma wifi (el punto de acceso del móvil);\n"
                  "  - Ollama está abierto en el portátil y escucha en la red (OLLAMA_HOST=0.0.0.0);\n"
                  "  - en el portátil esa red está marcada como privada (o el cortafuegos deja pasar el puerto 11434).\n"
                  "Si sabes la IP: python demo.py --url http://IP:11434 · Sin red: python demo.py --sin-model")
            sys.exit(1)
        LAST_URL.write_text(url, encoding="utf-8")
        print(f"  Encontrado en {url} (Ollama {version})")
        ollama = Ollama(url)
        missing = [m for m in (MODEL, EMBEDDING_MODEL) if not ({m, f"{m}:latest"} & ollama.modelos())]
        if missing:
            print(f"{YELLOW}Al portátil le missing modelos: {', '.join(missing)}{FIN}. En el portátil: ollama pull <model>")
            sys.exit(1)
        print(f"Cargando {MODEL} en memoria (la primera vez puede tardar un minuto)...", end=" ", flush=True)
        t0 = time.time()
        ollama.precargar()
        print(f"listo en {time.time() - t0:.0f} s")
        retrieval.embed = ollama.embed  # embeddings con keep_alive: el model no se descarga entre preguntas
    else:
        retrieval.embed = lambda textos: None

    fragments = load_corpus(DATA / "corpus")
    valid_count = sum(f.is_valid(today) for f in fragments)
    print(f"Indexando {len(fragments)} fragments ({valid_count} valid_count a {today}; lo caducado nunca se recupera)...", end=" ", flush=True)
    try:
        indice = Index(fragments)
        print("listo" + ("" if indice.vec else " (solo búsqueda léxica)"))
    except Exception as e:
        print(f"\n{YELLOW}Sin embeddings ({e}); sigo solo con búsqueda léxica.{FIN}")
        retrieval.embed = lambda textos: None
        indice = Index(fragments)

    print(f"\nEscribe una question y pulsa Intro. {GRAY}«salir» para terminar · Ctrl+C corta una answer.{FIN}")
    interactive = sys.stdin.isatty()
    while True:
        try:
            query = input(f"\n{GREEN}Pregunta>{FIN} ").strip().lstrip("﻿").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not interactive:
            print(query)
        if query.lower() in ("salir", "exit", "quit"):
            break
        if not query:
            continue
        try:
            handle_query(query, indice, ollama, today)
        except Exception as e:  # un fallo no tumba la demo: se ve el error y se sigue
            print(f"\n  {YELLOW}Error: {e!r}{FIN}")


if __name__ == "__main__":
    main()
