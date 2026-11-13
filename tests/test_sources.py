import json
from datetime import date

from app.sources import computrabajo as ct
from app.sources.bumeran import parse_item
from app.sources.jobspy_source import JobSpySource
from app.sources.base import SourceQuery
from tests.conftest import FIXTURES


def test_computrabajo_url():
    url = ct.build_search_url("https://pe.computrabajo.com", "Analista de Datos", "Lima", 2, 168)
    assert url == "https://pe.computrabajo.com/trabajo-de-analista-de-datos-en-lima?pubdate=7&p=2"
    assert ct.build_search_url("https://pe.computrabajo.com", "Ingeniería C#", "", 1, None) == "https://pe.computrabajo.com/trabajo-de-ingenieria-c"


def test_computrabajo_listing_parser():
    html = (FIXTURES / "computrabajo_list.html").read_text(encoding="utf-8")
    jobs = ct.parse_listing(html, "https://pe.computrabajo.com", today=date(2026, 11, 12))
    assert len(jobs) == 20
    j = jobs[0]
    assert j.title and j.url.startswith("https://pe.computrabajo.com/ofertas-de-trabajo/")
    assert j.company and j.location and j.date_posted == date(2026, 11, 12)
    assert len({x.external_id for x in jobs}) == 20
    assert all(x.location and any(c.isalpha() for c in x.location) for x in jobs)  # not the rating "4,1"
    assert jobs[0].location == "San Isidro, Lima"
    with_salary = [x for x in jobs if x.salary_min]
    assert with_salary and all(x.salary_currency == "PEN" for x in with_salary)
    assert any(x.modality_text for x in jobs)


def test_computrabajo_detail_parser():
    html = (FIXTURES / "computrabajo_detail.html").read_text(encoding="utf-8")
    info = ct.parse_detail(html)
    assert len(info["description"]) > 200
    assert "Requisitos" in info["description"]


def test_bumeran_item_parser():
    data = json.loads((FIXTURES / "bumeran_search.json").read_text(encoding="utf-8"))
    j = parse_item(data["content"][0], "https://www.bumeran.com.pe")
    assert j.title == "Analista de datos"
    assert j.url.endswith(f"-{data['content'][0]['id']}.html")
    assert j.date_posted == date(2026, 11, 11)
    assert j.modality_text == "Presencial" and len(j.description) > 200


def test_glassdoor_unavailable_for_peru():
    assert JobSpySource("glassdoor", "Glassdoor").availability("peru")
    assert JobSpySource("indeed", "Indeed").availability("peru") is None


def test_indeed_kwargs_respect_combination_rule():
    q = SourceQuery(term="x", location="Lima", country="peru", hours_old=72, limit=10, remote_only=True, job_type="tiempo_completo")
    kw = JobSpySource("indeed", "Indeed")._kwargs(q)
    assert kw["hours_old"] == 72 and "is_remote" not in kw and "job_type" not in kw
    kw = JobSpySource("linkedin", "LinkedIn")._kwargs(q)
    assert kw["location"] == "Lima, Perú" and kw["is_remote"] and kw["job_type"] == "fulltime"
