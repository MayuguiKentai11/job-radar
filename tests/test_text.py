from datetime import date

import pytest

from app.engine import text as T


@pytest.mark.parametrize("desc,expected", [
    ("Requisitos: 3 años de experiencia en análisis de datos", 3),
    ("Experiencia mínima de 2 años en posiciones similares.", 2),
    ("Contar con experiencia laboral de 5 años como mínimo", 5),
    ("Mínimo 4 años en el puesto", 4),
    ("3+ years of experience with SQL", 3),
    ("At least 2-4 years of relevant experience", 2),
    ("Dos años de experiencia en ventas", 2),
    ("Experiencia de 1 a 2 años en Power BI", 1),
    ("Sin experiencia, buscamos practicantes", None),
    ("Empresa con 18 años en el mercado", None),
])
def test_required_years(desc, expected):
    assert T.extract_required_years(desc) == expected


@pytest.mark.parametrize("txt,expected", [
    ("S/. 2.200,00 (Mensual)", (2200.0, None, "PEN", "monthly")),
    ("S/ 3,500 - 4,000 mensual", (3500.0, 4000.0, "PEN", "monthly")),
    ("USD 1500 a 2000", (1500.0, 2000.0, "USD", "monthly")),
    ("A convenir", (None, None, None, None)),
])
def test_salary(txt, expected):
    assert T.parse_salary_text(txt) == expected


def test_modality():
    assert T.detect_modality("Presencial y remoto", "Analista", "", None) == "hibrido"
    assert T.detect_modality("Remoto", "x", "", None) == "remoto"
    assert T.detect_modality(None, "Analista de datos - 100% remoto", "", None) == "remoto"
    assert T.detect_modality(None, "Analista", "Modalidad híbrida: 3 días en oficina. Trabajo híbrido", None) == "hibrido"
    assert T.detect_modality(None, "Analista", "Lugar: Miraflores", False) == "presencial"
    assert T.detect_modality(None, "Analista", "Lugar: Miraflores", None) is None


@pytest.mark.parametrize("title,level", [
    ("Analista BI Jr", "junior"), ("Data Engineer Senior", "senior"), ("Analista Semi Senior de Datos", "semi-senior"),
    ("Practicante de Analytics", "practicante"), ("Jefe de Inteligencia Comercial", "jefatura"), ("Analista de datos", None),
])
def test_level(title, level):
    assert T.detect_level(title) == level


def test_job_types():
    assert T.detect_job_types("Full-time", "", "") == {"tiempo_completo"}
    assert "temporal" in T.detect_job_types("Contrato Ocasional, Tiempo completo", "", "")
    assert T.detect_job_types(None, "Analista medio tiempo", "") == {"medio_tiempo"}


def test_relative_dates():
    today = date(2026, 11, 12)
    assert T.parse_relative_date("Hace  1  hora", today) == today
    assert T.parse_relative_date("Ayer", today) == date(2026, 11, 11)
    assert T.parse_relative_date("Hace 3 días", today) == date(2026, 11, 9)
    assert T.parse_relative_date("Hace más de 30 días", today) == date(2026, 10, 12)


def test_contains_term_accent_and_boundary():
    h = T.norm("Manejo de Python, SQL y inglés avanzado. Excel")
    assert T.contains_term(h, "ingles")
    assert T.contains_term(h, "Inglés")
    assert T.contains_term(h, "sql")
    assert not T.contains_term(h, "ex")  # no partial words
    assert T.contains_term(T.norm("Experiencia en C# y .NET"), "C#")


def test_language():
    assert T.detect_language("Buscamos un analista de datos con experiencia en la gestión de reportes para el área") == "es"
    assert T.detect_language("We are looking for a data analyst with experience in SQL and you will work with our team") == "en"
