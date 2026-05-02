# CLAUDE.md

Este archivo proporciona orientación a Claude Code (claude.ai/code) al trabajar con código en este repositorio.

## Reglas de Comportamiento de Claude

**01 — Pensá antes de actuar.** Leé los archivos existentes antes de escribir código. Evita sobreescribir cosas sin entender el contexto.

**02 — Sé conciso en el output.** Exhaustivo en el razonamiento interno, no en lo que se muestra. La respuesta que ve el usuario es corta; el proceso detrás, profundo.

**03 — Preferí editar antes que reescribir.** Si hay que cambiar 3 líneas, cambia 3 líneas. No reescribir el archivo completo.

**04 — No releas archivos sin motivo.** Si ya se leyeron en este contexto, no volver a leerlos a menos que hayan cambiado. Ahorra tokens de input.

**05 — Testeá el código antes de darlo por listo.** No declarar victorias prematuras. Verificar que funciona.

**06 — Sin openers sycofánticos ni cierre con frases vacías.** Elimina el 80% del ruido.

**07 — Mantené las soluciones simples y directas.** No over-engineering. No abstracciones que no se pidieron. La solución más simple que funciona.

**08 — Las instrucciones del usuario siempre tienen prioridad.** Si el usuario pide algo distinto a las reglas, sus instrucciones ganan. El archivo nunca limita al usuario.

## Descripción General del Proyecto

**Jacobo-Bot** es un sistema automatizado de marketplace inmobiliario que monitorea Idealista (plataforma española de propiedades), procesa datos de propiedades y publica listados en WordPress. También maneja búsquedas de compradores y las conecta con agencias colaboradoras.

**Flujo principal:**
1. Scrapear listados de propiedades de perfiles de agencias en Idealista
2. Comparar con base de datos SQLite local para detectar propiedades nuevas/actualizadas/eliminadas
3. Mejorar fotos de propiedades usando KIE.AI (eliminación de marcas de agua, mejora de imagen)
4. Publicar/actualizar/eliminar propiedades en WordPress (tema Houzez)
5. Procesar búsquedas de compradores en lenguaje natural y vincularlas con inventario o reenviarlas a agencias

## Arquitectura

### Módulos Principales

- **`scraper/`** — Scrapea perfiles de agencias en Idealista usando Playwright + Camoufox (anti-detección Firefox). Maneja captcha DataDome con soporte de pausa manual (modo `--show-browser`). Retorna instancias de `Property` (dataclass).

- **`monitor/`** — Loop principal de orquestación (`PropertyMonitor`). Ejecuta el ciclo completo: scrape → comparar → mejorar fotos → publicar en WP. Registra estadísticas (contador de nuevo/actualizado/eliminado).

- **`database/`** — Capa SQLite con cuatro tablas: `properties` (listados principales), `buyer_searches` (historial de búsquedas), `collaborating_agencies` (agencias asociadas), `scrape_runs` (historial de ejecuciones). Administrada por clase `Database`.

- **`photo_processor/`** — Mejora fotos de propiedades vía API KIE.AI. Elimina marcas de agua y mejora la calidad de imagen. Almacena URLs procesadas en la BD.

- **`wordpress/`** — Publica/actualiza/elimina propiedades como tipo de post Houzez en WordPress vía REST API. Maneja sincronización entre BD local y WP.

- **`search/`** — Dos componentes:
  - `SmartSearch`: Procesador de búsquedas de compradores impulsado por NLP (usa Claude de Anthropic si hay API key, sino usa regex). Vincula consultas con inventario.
  - `AgencyEmailSender`: Envía búsquedas no coincidentes a agencias colaboradoras vía SMTP.

- **`scheduler/`** — Wrapper de APScheduler. Ejecuta el trabajo monitor cada N horas (24h por defecto). Se bloquea indefinidamente.

- **`config/`** — Cargador de configuración desde `.env`. Toda la config externa centralizada aquí.

- **`dashboard/`** — Muestra estadísticas: propiedades por estado, historial de búsquedas, errores.

### Clases de Datos Clave

- `Property` — Metadatos de propiedad scrapeada (título, precio, ubicación, habitaciones, fotos, etc.)
- `RunStats` — Estadísticas de ejecución del monitor (encontradas, nuevas, actualizadas, eliminadas, errores)
- `SearchCriteria` — Búsqueda de comprador parseada (rango de precio, ubicación, tipo de propiedad, etc.)

## Comandos

### Script Principal

```bash
# Modo continuo: ejecutar trabajo monitor cada 24h (por defecto)
python main.py

# Ejecutar un ciclo de monitor y salir
python main.py --once

# Solo scrapear (omitir WP y procesamiento de fotos)
python main.py --scrape-only

# Scrapear con navegador visible (útil para debug de captcha)
python main.py --scrape-only --show-browser

# Procesar consulta de búsqueda de comprador
python main.py --search "Piso de 2 habitaciones en Málaga, 200k€" --email buyer@example.com --name "Juan"

# Mostrar dashboard de estadísticas
python main.py --dashboard
```

### Desarrollo

```bash
# Instalar dependencias
pip install -r requirements.txt

# Configurar entorno (copiar plantilla y llenar credenciales)
cp .env.example .env

# Ver logs
tail -f data/jacobo_bot.log
```

## Configuración

Toda la configuración vive en `.env`. Variables clave:

- **Idealista**: `IDEALISTA_PROFILE_URLS` (URLs de agencias a monitorear, separadas por coma)
- **KIE.AI**: `KIE_AI_API_KEY` (mejora de fotos), `MAX_PHOTOS_PER_PROPERTY` (control de costos)
- **WordPress**: `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`, `WP_PROPERTY_POST_TYPE=property` (Houzez), `WP_PROPERTY_REST_BASE=properties`
- **Base de Datos**: `DB_PATH` (ubicación SQLite, por defecto `data/jacobo_bot.db`)
- **SMTP**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` (para emails de agencias)
- **Agencias**: `COLLABORATING_AGENCIES` (array JSON de agencias asociadas)
- **Scheduler**: `CHECK_INTERVAL_HOURS` (frecuencia de monitoreo, 24 por defecto)
- **Claude API**: `ANTHROPIC_API_KEY` (opcional, habilita búsqueda NLP; omitir para solo regex)

## Conceptos Clave

### DataDome & Anti-Detección

El scraper usa **Camoufox** (basado en Firefox) como método principal para eludir DataDome. Si no está disponible, cae back a Playwright Chromium con plugins de stealth. Cuando aparece un captcha y `--show-browser` está activo, el scraper **se pausa automáticamente** hasta que lo resuelvas en el navegador visible—no hay intervención manual necesaria más allá del captcha mismo. Si tu IP está completamente bloqueada, prueba desde un hotspot móvil.

### Costos de Procesamiento de Fotos

KIE.AI cobra ~$0.027 por foto mejorada. `MAX_PHOTOS_PER_PROPERTY` limita el procesamiento para reducir costos. El sistema ignora planos y videos — solo procesa fotos de la propiedad.

### Documentación KIE.AI

- **API principal**: https://docs.kie.ai/market/seedream-5-lite-image-to-image
- **Referencia general**: https://docs.kie.ai/1973359m0
- **Modelo usado**: `seedream/5-lite-image-to-image`
- **Campos requeridos**: `model`, `input.prompt` (3–3000 chars), `input.image_urls` (array), `input.aspect_ratio` (`16:9` entre otros), `input.quality` (`"basic"` | `"high"`)
- **Límite por imagen**: 10 MB máximo, formatos: jpeg/png/webp
- **Códigos de error clave**: 422 = validación fallida (reintentable), 429 = rate limit, 402 = sin créditos, 501 = generación fallida
- **Polling**: endpoint `/api/v1/jobs/recordInfo`, estado de éxito `"success"`, estado de fallo `"fail"`

### Integración WordPress (Houzez)

Las propiedades se publican como tipo de post `property` de Houzez. Los campos se mapean a campos personalizados de Houzez (precio, ubicación, habitaciones, amenidades, fotos, etc.). Las actualizaciones detectan cambios mediante comparación de hash; solo las propiedades modificadas activan actualización en WP.

### Búsqueda en Lenguaje Natural

Si `ANTHROPIC_API_KEY` está configurada, las consultas de compradores usan Claude para coincidencia semántica (extracción de rango de precio, ubicación, tipo de propiedad). Si no, se usa fallback basado en regex. Las búsquedas no coincidentes se reenvían a agencias colaboradoras por email.

## Debug

- **Logs**: Toda la actividad se registra en `data/jacobo_bot.log` y stdout. Incluye scraper, llamadas API de WP, envíos de email y errores.
- **Debug de navegador**: Ejecuta `python main.py --scrape-only --show-browser` para ver la interacción del scraper con Idealista.
- **Consultas de BD**: Usa CLI de SQLite: `sqlite3 data/jacobo_bot.db "SELECT * FROM properties LIMIT 5;"`
- **Testing de email**: Verifica `SMTP_HOST`, `SMTP_PORT` y credenciales en `.env`. Los logs muestran intentos de envío.

## Dependencias

- **Scraping**: `requests`, `beautifulsoup4`, `lxml`, `playwright`, `camoufox`, `playwright-stealth`
- **Scheduling**: `APScheduler`
- **IA**: `anthropic` (para búsqueda NLP; opcional)
- **Entorno**: `python-dotenv`

## Problemas Típicos & Soluciones

| Problema | Solución |
|----------|----------|
| DataDome bloquea scraper | Bloqueo basado en IP. Prueba desde hotspot móvil o espera 24h. Usa `--show-browser` para resolver captcha manualmente. |
| Autenticación WP falla | Verifica `WP_APP_PASSWORD` (NO contraseña de login regular). Genérala en Admin WP > Usuarios > Tu Perfil > Application Passwords. |
| Fotos no mejoradas | Verifica que `KIE_AI_API_KEY` sea válida. Verifica saldo de crédito en API. Imágenes grandes pueden fallar—verifica presupuesto `MAX_PHOTOS_PER_PROPERTY`. |
| Email no enviado a agencias | Verifica credenciales SMTP en `.env`. Verifica que dominio del remitente tenga DKIM/SPF configurado (panel Hostinger). |
| Scheduler no se ejecuta | Asegúrate que no hay instancia previa bloqueando. Verifica logs de excepciones. `python main.py --once` testea un ciclo único. |
