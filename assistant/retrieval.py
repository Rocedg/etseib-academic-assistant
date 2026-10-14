"""Núcleo RAG: búsqueda híbrida (BM25 + embeddings si hay) con filtros de vigencia, domain y profile.
La métrica que manda aquí es recall@k sobre el banco de pruebas."""
import math
import os
import re
from collections import Counter
from datetime import date

from .corpus import Fragment
from .llm import embed


def _tokenize(t):
    return re.findall(r"\w+", t.lower())


class Index:
    def __init__(self, fragments: list[Fragment]):
        self.fragments = fragments
        self.docs = [_tokenize(f.section + " " + f.text) for f in fragments]
        self.avg = sum(map(len, self.docs)) / max(len(self.docs), 1)
        self.df = Counter(t for d in self.docs for t in set(d))
        self.vec = embed([f.text for f in fragments])   # None si el backend no da embeddings
        self.threshold = 0.02 if self.vec else 1.0

    def _bm25(self, q, i, k1=1.5, b=0.75):
        d, tf, n = self.docs[i], Counter(self.docs[i]), len(self.docs)
        return sum(math.log(1 + (n - self.df[t] + .5) / (self.df[t] + .5)) * tf[t] * (k1 + 1)
                   / (tf[t] + k1 * (1 - b + b * len(d) / self.avg)) for t in _tokenize(q) if t in tf)

    def search(self, query: str, today: date, domain: str | None = None,
               profile: str | None = None, k: int | None = None) -> list[tuple[Fragment, float]]:
        k = k or int(os.getenv("RAG_K", "5"))
        cand = [i for i, f in enumerate(self.fragments)
                if f.is_valid(today)                                   # nunca recuperar normativa caducada
                and (domain in (None, "documental") or f.domain == domain)
                and (not profile or not f.profiles or profile in f.profiles)]
        lex = {i: self._bm25(query, i) for i in cand}
        if self.vec:
            qv = embed([query])[0]
            cos = lambda a, b: sum(x * y for x, y in zip(a, b)) / (math.hypot(*a) * math.hypot(*b) or 1)
            sem = {i: cos(qv, self.vec[i]) for i in cand}
            # Fusión por rangos (RRF): no hay que calibrar escalas distintas.
            rank = lambda s: {i: r for r, i in enumerate(sorted(s, key=s.get, reverse=True))}
            rl, rs = rank(lex), rank(sem)
            score = {i: 1 / (60 + rl[i]) + 1 / (60 + rs[i]) for i in cand}
        else:
            score = lex
        # Aquí iría un reranker multilingüe sobre el top-20.
        best = sorted(cand, key=score.get, reverse=True)[:k]
        return [(self.fragments[i], score[i]) for i in best if score[i] > 0]
