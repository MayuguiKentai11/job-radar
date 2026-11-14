from app.sources.base import Source
from app.sources.bumeran import BumeranSource
from app.sources.computrabajo import ComputrabajoSource
from app.sources.jobspy_source import JobSpySource
from app.sources.serpapi_jobs import SerpApiJobsSource

SOURCES: dict[str, Source] = {
    s.key: s
    for s in [
        JobSpySource("indeed", "Indeed"),
        JobSpySource("linkedin", "LinkedIn"),
        ComputrabajoSource(),
        BumeranSource(),
        SerpApiJobsSource(),
    ]
}
