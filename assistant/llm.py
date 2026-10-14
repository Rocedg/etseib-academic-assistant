"""Capa de acceso al model. Todo el system llama a `llm_json` y nunca a un proveedor concreto,
así el model es un parámetro de configuración (Ollama local, API compatible OpenAI/Azure, o mock)."""
import json
import os
import urllib.error
import urllib.request

BACKEND = os.getenv("LLM_BACKEND", "mock")          # mock | ollama | openai
MODEL = os.getenv("LLM_MODEL", "gemma4:26b-a4b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OPENAI_URL = os.getenv("OPENAI_URL", "https://api.openai.com/v1")  # o un endpoint de Azure / Copilot, etc.
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")  # "none" = sin embeddings (solo BM25)
if EMBEDDING_MODEL.strip().lower() in ("", "none"):
    EMBEDDING_MODEL = ""
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "600"))     # en CPU una llamada puede pasar de 2 min
STATISTICS = []  # una entrada por llamada a Ollama: tokens y tiempos de lectura del prompt y generación


def _post(url, payload, headers=None):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def llm_json(system: str, user: str, schema: dict, model: str | None = None, task: str = "") -> dict:
    """Return a dictionary matching the supplied JSON schema."""
    model = model or MODEL
    if BACKEND == "ollama":
        body = {"model": model, "stream": False, "format": schema, "options": {"temperature": 0},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:  # sin "pensar": en CPU el razonamiento previo multiplica la latencia
            r = _post(f"{OLLAMA_URL}/api/chat", {**body, "think": False})
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            r = _post(f"{OLLAMA_URL}/api/chat", body)  # model sin modo de razonamiento
        ns = 1e9
        STATISTICS.append({"task": task, "model": model,
                             "tokens_prompt": r.get("prompt_eval_count", 0), "s_prompt": r.get("prompt_eval_duration", 0) / ns,
                             "tokens_gen": r.get("eval_count", 0), "s_gen": r.get("eval_duration", 0) / ns,
                             "s_carga": r.get("load_duration", 0) / ns, "s_total": r.get("total_duration", 0) / ns})
        try:
            return json.loads(r["message"]["content"])
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON inválido de {model} ({task}): {r['message']['content'][:200]!r}") from e
    if BACKEND == "openai":
        r = _post(f"{OPENAI_URL}/chat/completions", {
            "model": model, "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            {"Authorization": f"Bearer {OPENAI_KEY}"})
        return json.loads(r["choices"][0]["message"]["content"])
    return _mock(task, user)


def embed(textos: list[str]) -> list[list[float]] | None:
    """Embeddings multilingües vía Ollama (p. ej. qwen3-embedding:0.6b). None = solo búsqueda léxica."""
    if BACKEND != "ollama" or not EMBEDDING_MODEL:
        return None
    r = _post(f"{OLLAMA_URL}/api/embed", {"model": EMBEDDING_MODEL, "input": textos})
    return r["embeddings"]


def _mock(task, user):
    """Return deterministic responses for flow testing without a model."""
    lowered = user.lower()
    if task == "diagnostician":
        domain = ("learning_agreement" if any(k in lowered for k in ("learning", "erasmus", "movilidad", "mobilitat"))
                   else "enrollment" if any(k in lowered for k in ("matr", "asignatura", "assignatura")) else "documental")
        category = "human_support" if any(k in lowered for k in ("hablar con", "persona", "queja", "recurso")) else "informational"
        return {"domain": domain, "tipo": category, "language": "es", "profile": None, "missing_data": []}
    if task == "agent":
        return {"answer": "Según la fuente [1], consulta el procedimiento indicado.", "citations": [1],
                "next_actions": ["Revisar la fuente [1]"], "warnings": []}
    if task == "supervisor":
        return {"soportada": True, "lenguaje_de_decision": False, "problemas": []}
    return {}
