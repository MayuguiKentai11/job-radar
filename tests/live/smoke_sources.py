"""Live smoke test: hits the real portals. Run: python -m tests.live.smoke_sources"""
import sys, time
from app.sources.base import SourceQuery
from app.sources.registry import SOURCES

term = sys.argv[1] if len(sys.argv) > 1 else "analista de datos"
for key, src in SOURCES.items():
    t = time.time()
    msg = src.availability("peru")
    if msg:
        print(f"{key:13} NO DISPONIBLE: {msg}"); continue
    q = SourceQuery(term=term, location="Lima", country="peru", hours_old=168, limit=25, fetch_descriptions=True)
    try:
        jobs = src.search(q, lambda n, m: None)
        withdesc = sum(1 for j in jobs if j.description and len(j.description) > 80)
        dated = sum(1 for j in jobs if j.date_posted)
        sal = sum(1 for j in jobs if j.salary_min)
        print(f"{key:13} {len(jobs):3} ofertas | desc {withdesc} | fecha {dated} | salario {sal} | {time.time()-t:5.1f}s | ej: {jobs[0].title[:45] if jobs else '-'} @ {jobs[0].company if jobs else ''}")
    except Exception as e:
        print(f"{key:13} ERROR {type(e).__name__}: {str(e)[:150]} ({time.time()-t:.1f}s)")
