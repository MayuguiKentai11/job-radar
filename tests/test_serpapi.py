import json

import httpx
import pytest

from app.sources.base import SourceError, SourceQuery
from app.sources.serpapi_jobs import SerpApiJobsSource, parse_item, valid_key

KEY = "a" * 64

ITEM = {
    "title": "Analista de Datos BI", "company_name": "Banco X", "location": "Lima, Perú", "via": "vía LinkedIn",
    "description": "Buscamos analista con Power BI y SQL. " * 5, "job_id": "eyJqb2IiOiIxIn0=",
    "detected_extensions": {"posted_at": "hace 3 días", "schedule_type": "Tiempo completo", "salary": "S/ 4,000–S/ 5,000 al mes"},
    "apply_options": [{"title": "LinkedIn", "link": "https://pe.linkedin.com/jobs/view/123"}],
}


LOCS = [{"canonical_name": "Lima,Ohio,United States", "country_code": "US"},
        {"canonical_name": "Lima,Lima,Lima Province,Peru", "country_code": "PE"}]


def make(handler):
    def route(req):
        if req.url.path.endswith("/locations.json"):
            return httpx.Response(200, json=LOCS if req.url.params["q"] == "Lima" else [])
        return handler(req)

    return SerpApiJobsSource(client_factory=lambda: httpx.Client(transport=httpx.MockTransport(route)))


def q(**kw):
    base = dict(term="Analista de datos", location="Lima", country="peru", hours_old=None, limit=20, api_key=KEY)
    base.update(kw)
    return SourceQuery(**base)


def test_parse_item_maps_fields():
    j = parse_item(ITEM)
    assert j.source == "google" and j.url == "https://pe.linkedin.com/jobs/view/123"
    assert j.extra["via"] == "LinkedIn" and j.company == "Banco X"
    assert (j.salary_min, j.salary_max, j.salary_currency) == (4000, 5000, "PEN")
    assert j.date_posted is not None and j.job_type == "Tiempo completo"
    assert parse_item({"title": "x"}) is None  # no link


def test_search_paginates_and_sends_expected_params():
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        if "next_page_token" in req.url.params:
            return httpx.Response(200, json={"jobs_results": [{**ITEM, "job_id": "2"}]})
        return httpx.Response(200, json={"jobs_results": [ITEM], "serpapi_pagination": {"next_page_token": "tok"}})

    jobs = make(handler).search(q(), lambda n, m: None)
    assert len(jobs) == 2 and len(calls) == 2
    assert calls[0]["engine"] == "google_jobs" and calls[0]["location"] == "Lima,Lima,Lima Province,Peru" and calls[0]["gl"] == "pe"
    assert calls[1]["next_page_token"] == "tok"


def test_unknown_location_falls_back_to_country():
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        return httpx.Response(200, json={"jobs_results": [ITEM]})

    jobs = make(handler).search(q(location="Xyz", limit=10), lambda n, m: None)
    assert len(jobs) == 1 and calls[0]["q"] == "Analista de datos Xyz" and calls[0]["location"] == "Peru"


@pytest.mark.parametrize("status,error,msg", [
    (401, "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key", "no es válida"),
    (429, "Your account has run out of searches.", "se acabaron"),
])
def test_errors_are_friendly(status, error, msg):
    with pytest.raises(SourceError, match=msg):
        make(lambda r: httpx.Response(status, json={"error": error})).search(q(), lambda n, m: None)


def test_no_results_is_not_an_error():
    src = make(lambda r: httpx.Response(200, json={"error": "Google hasn't returned any results for this query."}))
    assert src.search(q(), lambda n, m: None) == []


def test_requires_key():
    with pytest.raises(SourceError):
        make(lambda r: httpx.Response(200, json={})).search(q(api_key=None), lambda n, m: None)
    assert valid_key(KEY) and not valid_key("abc") and not valid_key("a b" * 20)
