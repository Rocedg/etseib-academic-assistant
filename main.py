"""Uso: python main.py "¿Hasta cuándo puedo modificar la matrícula?" --profile grado [--today 2026-10-01]"""
import argparse
import json
from datetime import date
from pathlib import Path

from assistant.corpus import load_corpus
from assistant.pipeline import handle_query
from assistant.retrieval import Index

DATA = Path(__file__).parent / "data"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--profile")
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args()
    indice = Index(load_corpus(DATA / "corpus"))
    faq = json.loads((DATA / "faq.json").read_text(encoding="utf-8"))
    print(json.dumps(handle_query(a.query, indice, faq, date.fromisoformat(a.today), a.profile),
                     ensure_ascii=False, indent=2, default=str))
