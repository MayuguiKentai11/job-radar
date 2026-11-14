from datetime import date, timedelta

from app.engine.filters import apply_filters, dedupe, enrich, score
from app.engine.search import process
from app.engine.params import SearchParams
from app.sources.base import JobPosting

TODAY = date.today()


def job(**kw):
    base = dict(source="indeed", external_id=kw.pop("eid", "1"), title="Analista de Datos", url="https://x/1",
                company="ACME", location="Lima", description="Buscamos analista con SQL y Power BI. " * 5,
                date_posted=TODAY)
    base.update(kw)
    return JobPosting(**base)


def check(j, **params):
    p = SearchParams(keywords=["analista de datos"], **params)
    return apply_filters(enrich(j, p.currency), p)


def test_passes_basic():
    assert check(job()) is None


def test_date_filter():
    assert check(job(date_posted=TODAY - timedelta(days=10)), hours_old=168) == "Fuera del rango de fecha"
    assert check(job(date_posted=TODAY - timedelta(days=5)), hours_old=168) is None


def test_modality_filter_keeps_unknown():
    assert check(job(modality_text="Presencial"), modalities=["remoto"]).startswith("Modalidad")
    assert check(job(modality_text="Remoto"), modalities=["remoto", "hibrido"]) is None
    assert check(job(is_remote=None), modalities=["remoto"]) is None  # unknown → kept


def test_years_filter():
    j = job(description="Requisitos: 5 años de experiencia en BI. " * 4)
    assert check(j, years_experience=2) == "Pide 5 años de experiencia"
    assert check(j, years_experience=6) is None
    assert check(j, years_experience=0) == "Pide 5 años de experiencia"


def test_salary_filter_and_currency():
    assert check(job(salary_min=2000, salary_currency="PEN", salary_period="monthly"), salary_min=3000) == "Salario menor al mínimo"
    assert check(job(salary_min=1000, salary_currency="USD", salary_period="monthly"), salary_min=3000) is None  # 3750 PEN
    assert check(job(salary_min=60000, salary_currency="PEN", salary_period="yearly"), salary_min=4000) is None  # 5000/mes
    assert check(job(), salary_min=3000, include_no_salary=False) == "Sin salario publicado"
    assert check(job(), salary_min=3000) is None


def test_words_filters():
    assert check(job(), must_have="SQL, Python") == "Falta: Python"
    assert check(job(), must_have="sql") is None
    assert check(job(description="Puesto de ventas con comisiones atractivas " * 4), exclude="comisiones") == "Contiene 'comisiones'"
    assert check(job(title="Analista de Datos Senior"), exclude_title="senior") == "Título contiene 'senior'"
    assert check(job(company="Manpower Perú"), blocked_companies="manpower").startswith("Empresa bloqueada")


def test_must_have_without_description_is_kept():
    assert check(job(description=None), must_have="Python") is None


def test_level_and_language():
    assert check(job(title="Jefe de Analistas de Datos"), levels=["junior", "semi-senior"]) == "Nivel jefatura"
    en = job(description="We are looking for a data analyst with experience in SQL and you will work with our team. " * 3)
    assert check(en, languages=["es"]) == "Idioma en"


def test_score_ranks_better_matches_higher():
    p = SearchParams(keywords=["analista de datos"], nice_to_have="Power BI, Python, inglés", modalities=["remoto"])
    good = enrich(job(title="Analista de Datos", modality_text="Remoto",
                      description="Power BI, Python e inglés intermedio. " * 4), "PEN")
    weak = enrich(job(title="Asistente Administrativo", modality_text="Presencial",
                      description="Archivo y atención al cliente. " * 4, date_posted=TODAY - timedelta(days=20)), "PEN")
    sg, rg = score(good, p)
    sw, _ = score(weak, p)
    assert sg >= 90 and sw < 40 and sg > sw
    assert any("Menciona" in r for r in rg) and any("cargo coincide" in r for r in rg)


def test_dedupe_merges_across_portals():
    a = enrich(job(source="indeed", eid="a", title="Analista de Datos Jr", company="Banco Ripley Perú", description="x" * 300), "PEN")
    b = enrich(job(source="linkedin", eid="b", title="Analista de Datos Jr.", company="Banco Ripley Peru", description="corta"), "PEN")
    c = enrich(job(source="computrabajo", eid="c", title="Analista de Datos Jr", company="Otra Empresa"), "PEN")
    out = dedupe([b, a, c])
    assert len(out) == 2
    merged = next(e for e in out if e.job.source == "indeed")
    assert merged.also_on == ["linkedin"]


def test_process_pipeline_counts():
    p = SearchParams(keywords=["analista de datos"], exclude="ventas", min_score=10)
    jobs = [job(eid=str(i), title=f"Analista de Datos {i}", company=f"C{i}") for i in range(5)]
    jobs.append(job(eid="v", title="Analista de datos comerciales", company="V", description="Ventas y comisiones " * 10))
    jobs.append(job(eid="g", title="Analista de Inteligencia Artificial Generativa", company="G"))
    kept, rejected, dupes = process(jobs, p)
    assert len(kept) == 5 and dupes == 0
    assert rejected["Contiene 'ventas'"] == 1
    assert rejected["El cargo no coincide con lo buscado"] == 1
    assert kept == sorted(kept, key=lambda e: e.score, reverse=True)


def test_title_must_match_role_or_synonym():
    from app.engine.text import title_matches
    kw = "Analista de datos"
    for t in ["Analista de Datos", "Data Analyst", "Analista BI Semi Senior – Power BI", "ANALISTA SR. DE DATA ANALYTICS",
              "Analistas de datos", "Analista de Datos BI (Power BI)"]:
        assert title_matches(t, kw), t
    for t in ["Analista de Inteligencia Artificial Generativa", "Especialista de Analítica de Talento",
              "Especialista de Pricing", "Analista de Inteligencia Comercial", "Asistente Administrativo"]:
        assert not title_matches(t, kw), t
    assert title_matches("Ingeniera de Software", "Ingeniero de software")
    assert title_matches("HR Business Partner", "Recursos Humanos")
    assert title_matches("Desarrolladora Python", "desarrollador python")


def test_similar_titles_option_relaxes_the_title_filter():
    off_topic = job(title="Analista de Inteligencia Artificial Generativa")
    assert check(off_topic) == "El cargo no coincide con lo buscado"
    assert check(off_topic, similar_titles=True) is None


def test_dedupe_company_name_variants():
    a = enrich(job(eid="1", source="computrabajo", company="Laureate", title="UPC Remoto: Analista de Datos"), "PEN")
    b = enrich(job(eid="2", source="bumeran", company="Laureate Perú", title="UPC Remoto: Analista de Datos"), "PEN")
    assert len(dedupe([a, b])) == 1


def test_google_results_are_shown_as_their_original_portal():
    from app.engine.search import to_result

    li = enrich(job(source="google", eid="g1", url="https://pe.linkedin.com/jobs/view/1", extra={"via": "LinkedIn"}), "PEN")
    other = enrich(job(source="google", eid="g2", url="https://empresa.pe/empleo", extra={"via": "Trabajando.pe"}), "PEN")
    r1, r2 = to_result(li), to_result(other)
    assert r1["source"] == "linkedin" and r1["via_google"] is True and r1["via"] is None
    assert r2["source"] == "google" and r2["via_google"] is False and r2["via"] == "Trabajando.pe"
