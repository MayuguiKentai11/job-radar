# JobRadar — buscador de empleos en varios portales (Perú)

Busca cientos de ofertas a la vez en **Indeed, LinkedIn, Computrabajo y Bumeran**. Las filtra según tus preferencias,
elimina duplicados entre portales, las ordena por un **puntaje de relevancia (0-100)** y, si quieres, las compara con
tu **CV usando IA (Google Gemini, o Claude como alternativa)**.

## Iniciar
Haz doble clic en **`run.bat`**. La primera vez crea el entorno e instala dependencias, lo que toma 1-2 minutos.
Luego abre `http://localhost:8000`. Para detener la app, cierra la ventana negra.

Manual: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt && .venv\Scripts\python -m uvicorn app.main:app`

## Cómo usarlo
1. **Buscar**
   - **A. Qué buscar:** cargos (cada uno se busca por separado), ciudad, modalidad, antigüedad, jornada y portales.
   - **B. Perfil:** nivel, salario mínimo (PEN o USD, convierte entre monedas) y tus años de experiencia.
   - **C. Filtros finos:** palabras obligatorias, deseables y excluyentes, títulos a excluir, empresas bloqueadas e idioma.
   - **D. Extras:** puntaje mínimo, mostrar solo nuevas y análisis con IA.
   - **Guardar perfil** guarda la combinación para repetirla con un clic.
2. **Resultados:** filtros rápidos sin volver a buscar. Al hacer clic en una oferta ves por qué obtuvo su puntaje, la descripción con tus palabras resaltadas (verde = deseables/obligatorias, rojo = excluyentes), los estados (Interesante, Postulé, Entrevista, Descartada) y notas. Desde aquí exportas a Excel o CSV.
3. **Mis ofertas:** tablero con tus postulaciones por estado.
4. **Configuración:** sube tu CV (PDF, DOCX o TXT), elige el proveedor de IA (Gemini por defecto) y pega tu API key.

Si una oferta no aparece, el panel "Descartadas por filtros" muestra cuántas se eliminaron y por qué.

## Cómo se calcula el puntaje
Título vs. cargos (35) + palabras deseables (30) + modalidad (15) + salario (10) + recencia (10).
Solo cuentan los criterios que llenaste, y el resultado se normaliza a 100.

## Portales
| Portal | Estado | Notas |
|---|---|---|
| Indeed | ✅ | Rápido; muchas ofertas traen salario |
| LinkedIn | ✅ | Más lento (unos 30 s por cada 25 ofertas con descripción). Bloquea temporalmente si abusas. |
| Computrabajo | ✅ | Scraper propio: listado más detalle de cada oferta |
| Bumeran | ✅ | Usa la API interna del sitio |
| Google Jobs | ⚠ experimental | Google bloquea actualmente las consultas automáticas (devuelve 0) |
| Glassdoor | ❌ | No tiene sitio para Perú en el motor usado |

Si un portal cambia su página o te bloquea, los demás siguen funcionando, y el panel de progreso muestra el error de cada portal.

## IA (opcional)
Analiza las N ofertas con mayor puntaje (30 por defecto) frente a tu CV y devuelve afinidad, fortalezas, brechas y
cómo enfocar la postulación. Los resultados se guardan en caché: no se paga dos veces por la misma oferta con el
mismo CV y modelo.

**Obtener tu API key de Gemini:**
1. Entra a https://aistudio.google.com/apikey con tu cuenta de Google.
2. Clic en **Create API key**. Elige o crea un proyecto y copia la key (empieza con `AIza`).
3. En JobRadar → **Configuración**, elige **Google Gemini**, pega la key y haz clic en **Guardar**.

Modelos: **Gemini 3.8 Flash** (recomendado), **3.5 Flash-Lite** (más barato) o **Pro** (mejor calidad). El nivel
gratuito de Gemini tiene límites por minuto y por día. Si los alcanzas, la app te lo indica: reduce las
"ofertas a analizar" o espera un momento. Claude sigue disponible como proveedor alternativo.

## Tests
```
.venv\Scripts\python -m pytest -q                # 53 pruebas offline
.venv\Scripts\python -m tests.live.smoke_sources  # prueba en vivo contra los portales
```

Tus datos (ofertas, estados, CV y key) se guardan solo en `data/jobradar.db`, en tu computadora.
Herramienta de uso personal: usa frecuencias moderadas y respeta los términos de cada portal.
