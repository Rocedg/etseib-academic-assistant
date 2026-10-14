"""Diagnostic, coordination, retrieval-agent, tool, and supervisor components."""
import json
import os
import re
from datetime import date

from .llm import llm_json

SMALL_MODEL = os.getenv("LLM_SMALL_MODEL") or None  # diagnosticador y supervisor; None = el principal

# ---------- Diagnosticador ----------
DIAG_SCHEMA = {"type": "object", "required": ["domain", "tipo", "language", "profile", "missing_data"], "properties": {
    "domain": {"enum": ["enrollment", "learning_agreement", "documental"]},
    "tipo": {"enum": ["informational", "guided_task", "incident", "human_support"]},
    "language": {"enum": ["ca", "es", "en"]},
    "profile": {"enum": ["grado", "master", "doctorado", "movilidad_entrante", "movilidad_saliente", None]},
    "missing_data": {"type": "array", "items": {"type": "string"}}}}

DIAG_SYSTEM = """Eres el diagnosticador de un asistente académico de la ETSEIB. Clasifica la consulta.
No respondas a la pregunta. Si el estudiante pide hablar con una persona, tipo = human_support.
Si la respuesta depende del perfil y no se conoce, añade "profile" a missing_data."""


def diagnose(query: str, profile: str | None) -> dict:
    return llm_json(DIAG_SYSTEM, f"Perfil declarado: {profile}\nConsulta: {query}", DIAG_SCHEMA,
                    model=SMALL_MODEL, task="diagnostician")


# ---------- Coordinador: reglas, no LLM ----------
AGENTS = {"enrollment": "Matrícula", "learning_agreement": "Learning Agreement", "documental": "Búsqueda documental"}


def coordinate(diag: dict) -> str:
    if diag["tipo"] in ("human_support", "incident"):
        return "escalate"
    if diag["missing_data"]:
        return "clarify"
    return diag["domain"]


def find_faq(query: str, faq: list[dict]) -> dict | None:
    """Look up a validated FAQ using simple lexical matching."""
    q = set(re.findall(r"\w+", query.lower()))
    for f in faq:
        keys = set(f["keys"])
        if len(q & keys) / len(keys) >= 0.8:
            return f
    return None


# ---------- Herramienta determinista de plazos ----------
def deadline_status(start: date, end: date, today: date) -> str:
    """The exact date calculation is done here, not by the LLM."""
    if today < start:
        return f"aún no abierto (abre en {(start - today).days} días)"
    if today <= end:
        return f"abierto (cierra en {(end - today).days} días)"
    return f"cerrado desde hace {(today - end).days} días"


def annotate_deadlines(text: str, today: date) -> str:
    """Busca rangos 'AAAA-MM-DD a AAAA-MM-DD' en un fragmento y añade su estado respecto a today."""
    def sub(m):
        start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        return f"{m.group(0)} [{deadline_status(start, end, today)}]"
    return re.sub(r"(\d{4}-\d{2}-\d{2}) a (\d{4}-\d{2}-\d{2})", sub, text)


# ---------- Agente especializado ----------
AGENT_SCHEMA = {"type": "object", "required": ["answer", "citations", "next_actions", "warnings"], "properties": {
    "answer": {"type": "string"}, "citations": {"type": "array", "items": {"type": "integer"}},
    "next_actions": {"type": "array", "items": {"type": "string"}},
    "warnings": {"type": "array", "items": {"type": "string"}}}}

AGENT_SYSTEM = """Eres el agente de {name} del asistente académico de la ETSEIB. Hoy es {today}.
Responde SOLO con la información de los fragmentos numerados. Cita cada dato con [n].
Si los fragmentos no contienen la respuesta, dilo explícitamente y no la inventes.
Distingue lo que recomiendas de lo que decide la universidad. Responde en {language}."""


def answer(agent: str, query: str, fragments, diag: dict, today: date) -> dict:
    context = "\n\n".join(f"[{n}] ({f.source}, {f.section}, versión {f.version})\n{annotate_deadlines(f.text, today)}"
                           for n, (f, _) in enumerate(fragments, 1))
    system = AGENT_SYSTEM.format(name=AGENTS[agent], today=today.isoformat(), language=diag["language"])
    return llm_json(system, f"Fragmentos:\n{context}\n\nConsulta: {query}", AGENT_SCHEMA, task="agent")


# ---------- Supervisor: reglas + verificación con LLM ----------
SUPERVISOR_SCHEMA = {"type": "object", "required": ["soportada", "lenguaje_de_decision", "problemas"], "properties": {
    "soportada": {"type": "boolean"}, "lenguaje_de_decision": {"type": "boolean"},
    "problemas": {"type": "array", "items": {"type": "string"}}}}

SUPERVISOR_SYSTEM = """Eres un verificador. Comprueba si CADA afirmación de la answer está respaldada por los fragments.
Marca lenguaje_de_decision si la answer promete decisiones de la universidad ("te lo aprobarán")."""

def supervise(response: dict, fragments, today: date, threshold: float) -> tuple[str, list[str]]:
    reasons = []
    # Reglas deterministas (baratas y fiables)
    if not fragments or fragments[0][1] < threshold:
        reasons.append("recuperación débil")
    if not response["citations"] or any(c < 1 or c > len(fragments) for c in response["citations"]):
        reasons.append("sin citations válidas")
    if any(not f.is_valid(today) for f, _ in fragments):
        reasons.append("source no is_valid")
    if reasons:
        return "escalate", reasons
    # Verificación con LLM (faithfulness)
    ctx = "\n".join(f"[{n}] {f.text}" for n, (f, _) in enumerate(fragments, 1))
    v = llm_json(SUPERVISOR_SYSTEM, f"Fragmentos:\n{ctx}\n\nRespuesta:\n{json.dumps(response, ensure_ascii=False)}",
                 SUPERVISOR_SCHEMA, model=SMALL_MODEL, task="supervisor")
    if not v["soportada"]:
        return "escalate", ["afirmaciones sin respaldo"] + v["problemas"]
    if v["lenguaje_de_decision"]:
        return "answer_with_warning", ["La decisión final corresponde a la universidad."]
    return "answer", []


# ---------- Derivación ----------
UNITS = {"enrollment": "Gestión académica (SIAE)", "learning_agreement": "Oficina de movilidad",
            "documental": "SIAE"}  # tabla de derivación: a validar con el SIAE


def create_ticket(query, diag, fragments, reasons):
    return {"summary": query[:200], "provided_data": {k: diag[k] for k in ("profile", "language")},
            "sources_consulted": [f"{f.source} ({f.version})" for f, _ in fragments],
            "pending_questions": reasons, "recommended_unit": UNITS.get(diag["domain"], "SIAE")}
