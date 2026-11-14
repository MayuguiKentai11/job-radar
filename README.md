# JobRadar — buscador de empleos en varios portales (Perú)

Busca cientos de ofertas a la vez en **Indeed, LinkedIn, Computrabajo y Bumeran**. Las filtra según tus preferencias,
elimina duplicados entre portales, **solo muestra las ofertas cuyo cargo coincide con lo que buscas** (o un sinónimo), las ordena por relevancia y, si quieres, las compara con
tu **CV usando IA** (Google Gemini, o Claude como alternativa).

## Arquitectura: sin base de datos
El servidor **no guarda nada**:

| Dato | Dónde vive |
|---|---|
| Sesión de acceso | Cookie firmada `__Host-jr_session` (HttpOnly, Secure, SameSite=Strict, 7 días) |
| API key de IA | Cookie **cifrada** `__Host-jr_ai` (Fernet/AES, HttpOnly, Secure, SameSite=Strict, 30 días). Nunca vuelve al navegador en claro. |
| Búsquedas (últimas 5), estados, notas, perfiles, CV, análisis IA | `localStorage` de **tu navegador** |

Los resultados de una búsqueda pesan cientos de KB, y una cookie solo admite unos 4 KB. Por eso esos datos viven en el
navegador, no en cookies. La consecuencia es que **los datos son por navegador**: en otro equipo empiezas de cero. En
Configuración puedes **borrar todos tus datos**.

## Seguridad
- **Acceso con contraseña** (`APP_PASSWORD`, mínimo 12 caracteres, obligatoria en producción). El login está limitado a 5 intentos cada 15 minutos por IP y la contraseña se compara en tiempo constante.
- **Secretos solo por variables de entorno.** La app se niega a arrancar en producción sin `SECRET_KEY` (32+ caracteres) ni `APP_PASSWORD`.
- **Protección CSRF:** cookies `SameSite=Strict`, cabecera personalizada obligatoria en todo POST y verificación de `Origin`.
- **Cabeceras de seguridad:** CSP estricta (sin scripts inline), HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP y `Cache-Control: no-store` en la API.
- **XSS:** el texto de las ofertas siempre se escapa antes de mostrarse, y los enlaces solo aceptan `http(s)` (se bloquean `javascript:` y `data:`).
- **Exportación a Excel/CSV:** se neutraliza la inyección de fórmulas (`=`, `+`, `-`, `@`).
- **Límites contra abuso:**
  - Hasta 2 búsquedas simultáneas en el servidor y 1 por IP.
  - 30 búsquedas y 20 análisis IA por hora.
  - Hasta 5 cargos por búsqueda y 100 ofertas por portal.
  - Cuerpo de la petición ≤ 2 MB; CV ≤ 5 MB.
- **Sin documentación de API pública** (`/docs` desactivado), sin cabecera `Server` y errores genéricos sin trazas.
- **Contenedor endurecido:** corre como usuario sin privilegios (uid 10001) con el código en solo lectura. Las dependencias están fijadas y auditadas con `pip-audit` (0 vulnerabilidades conocidas).
- **Limitación conocida:** la CSP incluye `'unsafe-eval'`, que Alpine.js necesita para evaluar sus expresiones. Se compensa sin scripts inline y escapando todo el contenido externo.

## Desplegar en Render (gratis)
1. Sube esta carpeta a un repositorio de GitHub (puede ser privado).
2. En https://dashboard.render.com: **New → Blueprint**, conecta el repositorio y Render leerá `render.yaml`.
3. Cuando lo pida, escribe tu **`APP_PASSWORD`** (12+ caracteres). `SECRET_KEY` se genera solo.
4. **Apply.** En unos 5 minutos tendrás tu URL `https://jobradar-xxxx.onrender.com`.

Plan gratuito: el servicio **se duerme tras 15 minutos sin uso**, y la primera visita tarda unos 50 segundos en despertarlo.
Los portales pueden tratar distinto las IPs de centros de datos que tu conexión de casa. Si un portal falla en Render,
el panel de progreso lo indica y los demás siguen funcionando.

## Uso local
Haz doble clic en **`run.bat`** y abre `http://localhost:8000`. En local no pide contraseña salvo que definas `APP_PASSWORD` en `.env`.
Para probar la imagen de producción:
`docker build -t jobradar . && docker run -p 10000:10000 -e SECRET_KEY=<48+ chars> -e APP_PASSWORD=<12+ chars> jobradar`

## Cómo usarlo
1. **Buscar**
   - **A. Qué buscar:** cargos, ciudad, modalidad, antigüedad, jornada y portales.
   - **B. Perfil:** nivel, salario mínimo (PEN o USD) y tus años de experiencia.
   - **C. Filtros finos:** palabras obligatorias, deseables y excluyentes, títulos a excluir, empresas bloqueadas e idioma.
   - **D. Extras:** ocultar ofertas ya vistas y análisis con IA.
   - Puedes guardar la combinación como perfil.
2. **Resultados:** filtros rápidos; al abrir una oferta ves por qué se muestra, la descripción resaltada, los estados y tus notas. Desde aquí exportas a Excel o CSV.
3. **Mis ofertas:** tablero con tus postulaciones por estado.
4. **Configuración:** CV, proveedor y modelo de IA, API key y borrado de datos.

**Cargo estricto:** «Analista de datos» muestra «Data Analyst», «Analista BI» o «Analista de Datos Sr.», pero no «Analista de Inteligencia Artificial». Marca *Incluir también cargos parecidos* si quieres más resultados.

**Orden:** primero las que coinciden con el cargo y mencionan tus palabras deseables, luego modalidad, salario y fecha.

## IA: tu API key de Gemini
1. Entra a https://aistudio.google.com/apikey y haz clic en **Create API key**. Empieza con `AIza`.
2. En JobRadar → **Configuración**: proveedor Google Gemini, pega la key y haz clic en **Guardar**.

Modelos: Gemini 3.8 Flash (recomendado), 3.5 Flash-Lite (más barato) o Pro. Si llegas al límite del plan gratuito, la app
reintenta automáticamente. Aviso: en el plan gratuito de Gemini, Google puede usar los datos enviados (incluido tu CV)
para mejorar sus productos.

## Portales
| Portal | Estado |
|---|---|
| Indeed, LinkedIn, Computrabajo, Bumeran | ✅ (LinkedIn es el más lento) |
| Google Jobs | ⚠ experimental: Google bloquea consultas automáticas |
| Glassdoor | ❌ sin sitio para Perú |

## Tests
```
pip install -r requirements-dev.txt
python -m pytest -q                  # 65 pruebas offline (incluye pruebas de seguridad)
python -m tests.live.smoke_sources   # prueba en vivo contra los portales
```
Herramienta de uso personal: usa frecuencias moderadas y respeta los términos de cada portal.
