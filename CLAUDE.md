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

**09 — Siempre hacer commit y dar el comando de pull para el VPS al terminar.** Después de cualquier cambio en el código, hacer `git add . && git commit && git push` automáticamente. Luego incluir al final de la respuesta el comando exacto para que el usuario lo ejecute en el VPS:
```
cd C:\Users\LIVETEAM\Desktop\jacobo-bot && git pull
```

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

### Cómo ejecutar en Windows (para el usuario, sin conocimientos técnicos)

1. Abrir el Explorador de archivos y navegar a la carpeta `jacobo-bot`
2. Clic en la barra de direcciones (donde pone la ruta), escribir `cmd` y pulsar Enter
3. En la ventana negra que aparece, escribir el comando y pulsar Enter:
   - **Prueba de 1 ciclo completo:** `python main.py --once`
   - **Modo continuo (cada 72h):** `python main.py`
   - **Solo scrapear sin publicar:** `python main.py --scrape-only`
   - **Ver estadísticas:** `python main.py --dashboard`
   - **Diagnóstico WP listing (por qué algunas no aparecen en listing-v6-full-width):** `python -m tools.diagnose_wp_listing`
   - **Diagnóstico + arreglar status meta automáticamente:** `python -m tools.diagnose_wp_listing --fix-meta`
   - **Verificar fotos crudas en WP (dry-run):** `python -m tools.verify_uploaded`
   - **Verificar y reprocesar automáticamente:** `python -m tools.verify_uploaded --reprocess`
   - **Purgar cache de WP (si la web no muestra cambios):** `python -m tools.purge_cache`
4. No cerrar la ventana negra mientras se ejecuta

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

- **Idealista (perfiles + códigos)**: tabla `configuracion/perfiles.txt` — una línea por agencia con formato `NOMBRE | URL_IDEALISTA | CODIGO_INTERNO`. Esa tabla define qué se scrapea y qué código corto aparece en `fave_property_id` (oculta el nombre de la agencia origen). Si la tabla no existe, fallback al `.env` con `IDEALISTA_PROFILE_URLS` + `AGENCY_CODES` (JSON).
- **KIE.AI**: `KIE_AI_API_KEY` (mejora de fotos), `MAX_PHOTOS_PER_PROPERTY` (control de costos)
- **WordPress**: `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`, `WP_PROPERTY_POST_TYPE=property` (Houzez), `WP_PROPERTY_REST_BASE=properties`
- **Base de Datos**: `DB_PATH` (ubicación SQLite, por defecto `data/jacobo_bot.db`)
- **SMTP**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` (para emails de agencias)
- **Agencias**: `COLLABORATING_AGENCIES` (array JSON de agencias asociadas)
- **Scheduler**: `CHECK_INTERVAL_HOURS` (frecuencia de monitoreo, 24 por defecto)
- **Claude API**: `ANTHROPIC_API_KEY` (opcional, habilita búsqueda NLP; omitir para solo regex)

## Conceptos Clave

### DataDome & Anti-Detección

**NO usar Camoufox.** El método de anti-detección es exclusivamente **Scrape.do** (via `SCRAPE_DO_TOKEN`).

Estrategia de cuentas Scrape.do:
- **Carga inicial**: rotar 3 cuentas para distribuir los ~2.440 tokens necesarios
- **Operación normal (cada 72h)**: rotar 2 cuentas activas (~280 tokens/ejecución), la tercera queda de reserva
- Si una cuenta se bloquea, se reemplaza por la de reserva sin más

### Costos de Procesamiento de Fotos

KIE.AI cobra ~$0.027 por foto mejorada. `MAX_PHOTOS_PER_PROPERTY` limita el procesamiento para reducir costos. El sistema ignora planos y videos — solo procesa fotos de la propiedad.

### Política Anti-Copyright (CRÍTICA)

**Nunca se sube a WordPress una foto sin procesar por KIE.AI.** Idealista puede demandar por subir originales con su marca de agua.

- Si KIE falla en cualquier foto (sin créditos, error, descarga rota), el enhancer devuelve lista vacía → la propiedad NO se publica en WP en este ciclo.
- La propiedad queda marcada en BD sin `wp_post_id` y se reintenta automáticamente en el siguiente ciclo.
- Para auditar propiedades ya publicadas: `python -m tools.verify_uploaded` detecta crudas con pHash + visión IA, y con `--reprocess` las arregla.

### Clasificador y selección de fotos

Cada foto pasa por OpenRouter vision que devuelve `{type, empty, shot}`:
- `type`: fachada / salon / cocina / dormitorio / bano / terraza / exterior / otro
- `empty`: True si está vacía (sin muebles) → activa Home Staging automático en esa foto
- `shot`: wide / close — se prefieren wide shots cuando hay varias del mismo tipo

Después de clasificar, se eliminan duplicados con pHash (hamming distance ≤ 6) dentro de cada bucket de room_type. Esto evita publicar la misma habitación desde 3 ángulos distintos.

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
- **Reporte por ciclo** (auditoría rápida): `data/processing_report.log` (humano) + `data/processing_report.jsonl` (máquina). Incluye por propiedad: fotos_scraped → selected → kie_sent → ok/fail → uploaded, con motivos de skip y notas.
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
| Email no enviado a agencias | Verifica credenciales SMTP en `.env`. Verifica que dominio del remitente tenga DKIM/SPF configurado (panel CDmon). |
| Scheduler no se ejecuta | Asegúrate que no hay instancia previa bloqueando. Verifica logs de excepciones. `python main.py --once` testea un ciclo único. |
| Web caída tras diagnose_wp_listing | El script hace 1 llamada XML-RPC por propiedad — con 50+ propiedades satura el servidor. **SIEMPRE usar `--limit 10` la primera vez.** Si la web cae, reiniciar desde panel CDmon o esperar 5-10 min hasta que PHP libere procesos. |

### ⚠️ Regla crítica: herramientas de diagnóstico en producción

`tools/diagnose_wp_listing` y `tools/verify_uploaded` hacen muchas llamadas HTTP al servidor WordPress en serie. Ejecutarlas sin límite sobre una web en producción **puede tumbar el servidor**.

**Protocolo obligatorio antes de correr estas herramientas:**
1. Avisar al cliente que la web puede ponerse lenta 2-3 minutos
2. Correr siempre con `--limit 10` primero para verificar que funciona
3. Si hay >20 propiedades, agregar `time.sleep(0.5)` entre llamadas o correr en horario de bajo tráfico
4. Nunca correr `--reprocess` sin haber hecho primero un dry-run y revisado el CSV

## Incidentes resueltos (no repetir)

Lecciones documentadas de errores reales en producción. Antes de tocar estas áreas, lee la nota correspondiente.

### Anti-detección y scraping

**1. Scrape.do se banea con frecuencia.** Las cuentas free duran días/semanas antes de ser bloqueadas. Cuando un token marca 401 con `"inactive or incorrect"` no es un límite mensual — es ban definitivo de cuenta. No reintentar; rotar a otra cuenta o cambiar de servicio. Ver `_get_html_via_scrapedo` en [scraper/idealista_scraper.py](scraper/idealista_scraper.py).

**2. Proxies datacenter (Webshare, etc.) NO pasan DataDome de Idealista.** DataDome blacklistea por ASN (Server-Mania, Leaseweb…). Con curl falla con 403 inmediato; con Camoufox + JS challenge a veces pasa la primera vez pero se quema rápido. **Para Idealista usar Scrapfly** (proxies residenciales internos) o Camoufox con hotspot móvil. Webshare gratis solo sirve como fallback de emergencia.

**3. Scrapfly es la solución estable para DataDome.** ~25 créditos por request (sin `render_js`), free tier 1000/mes ≈ 40 requests. Para 30 propiedades por ciclo (listing + detail) gasta ~800. Si necesitan automatización 72h hay que pagar plan starter. Backend prioridad en [scraper/idealista_scraper.py](scraper/idealista_scraper.py): Scrapfly > Scrape.do > Camoufox.

### Configuración (.env y configuracion/)

**4. El parser de `.env` lee `#` como valor literal.** `SCRAPE_DO_TOKEN=#` deja `SCRAPE_DO_TOKEN` con valor `"#"` (truthy), activando modos rotos. Para desactivar una variable: **borrar la línea entera**, NO ponerle `#` al final. Comentarios solo válidos al inicio absoluto de línea.

**5. Los `.txt` de `configuracion/` tienen prioridad sobre `.env`.** Si una variable está en ambos, gana el `.txt`. Si edito el `.env` y no veo el cambio, comprobar que no existe duplicada en `configuracion/*.txt`. Ver `_get()` en [config/settings.py](config/settings.py).

**6. La carpeta `configuracion/` está en `.gitignore`.** `git pull` NUNCA trae los archivos de ahí. Cualquier cambio en `perfiles.txt`, `02_fotos.txt` etc. hay que replicarlo manualmente en el VPS.

### WordPress / Houzez

**7. `fave_property_status` requiere slug español en sitio WPML español.** El listing-v6-full-width filtra por slug del término, no por label. Si el sitio está en español, los slugs son `en-venta`/`en-alquiler`, no `for-sale`/`for-rent`. Propiedades con slug inglés no aparecen en el listing aunque tengan `status=publish`. Ver `_VALID_STATUS_SLUGS` en [tools/diagnose_wp_listing.py](tools/diagnose_wp_listing.py) y la asignación en [wordpress/property_publisher.py](wordpress/property_publisher.py).

**8. `fave_property_id` se ve público — no exponer agencia origen.** El cliente prohíbe mencionar el nombre real de la inmobiliaria de origen. Usar siempre el código corto definido en `AGENCY_CODES` (tabla `configuracion/perfiles.txt`). Para arreglar IDs viejos: `python -m tools.fix_property_ids --apply`.

**9. WP en CDmon (hosting compartido) limita XML-RPC.** Más de ~5 llamadas seguidas tumban el servidor PHP por 5-10 min. Cualquier loop de XML-RPC necesita: sleep ≥ 15s entre escrituras + reintentos con backoff 30s/60s/90s. Ver `_set_meta_with_retry` en [tools/fix_property_ids.py](tools/fix_property_ids.py).

### Fotos KIE.AI

**10. KIE devuelve fotos de 5-7 MB sin optimización.** Saturaba mediateca (~75MB/propiedad) y subidas tardaban 13s/foto. Solución aplicada en `_optimize_for_web` ([wordpress/property_publisher.py](wordpress/property_publisher.py)): resize a 1920px + JPEG q85 progressive → ~400KB/foto (-93%). **Nunca subir el original de KIE sin pasar por esta función.**

**11. Marcas de agua sutiles sobreviven KIE quality=basic.** Patrones "iiii" camuflados en paredes/fondos no se eliminan. Mantener `quality: "high"` en el payload + prompt explícito sobre patrones semi-transparentes. Si vuelve a aparecer una watermark residual: subir threshold de prompt antes de bajar quality.

**12. pHash threshold 10 era demasiado estricto para dedup.** Misma habitación desde ángulos ligeramente distintos pasaba el filtro. Subido a 14 (~22% diferencia). Ver `_PHASH_DUP_THRESHOLD` en [photo_processor/photo_classifier.py](photo_processor/photo_classifier.py).

**13. Home Staging aluciona muebles en exteriores.** KIE pone sofás encima de piscinas y muebles flotando en fachadas cuando `empty=True` se aplica a `terraza`/`exterior`/`jardin`. Restringir staging SOLO a interiores (`salon`, `cocina`, `dormitorio`). Para exteriores: usar `_ENHANCE_PROMPT` normal sin staging aunque la IA detecte vacío.

### Descripciones y contenido

**14. AI rewriter conservaba teléfonos y CTAs de contacto.** El prompt original decía "conserva TODOS los datos concretos" → conservaba `607792500`, "WhatsApp", "no dudes en contactar…". Solución: prompt actualizado + `_strip_contact_info` ([wordpress/property_publisher.py](wordpress/property_publisher.py)) que elimina con regex teléfonos/emails/URLs/oraciones-CTA antes Y después del rewrite. **Nunca confiar solo en el AI para sanitizar — siempre regex de seguridad después.**

### Detección de bajas / borrado en WP

**15. Un scrape fallido borraba propiedades válidas de WP (incidente grave).** El monitor detectaba bajas con `removed_ids = known_ids - all_seen_ids`. Si un perfil fallaba al scrapear (rate-limit de Scrapfly, ban, error de red), sus propiedades faltaban en `all_seen_ids` y se interpretaban como "desaparecidas de Idealista". Peor: la baja era un `delete_post(force=True)` = **borrado permanente** (ni a la papelera). Un solo rate-limit borró ~30 propiedades de una agencia. Soluciones:
  - **Las bajas ahora PAUSAN, no borran.** `_handle_paused` pone el post en `draft` (`publisher.pause`); `_handle_reappeared` lo reactiva a `publish` si la propiedad vuelve a Idealista. Nunca se usa `delete_post` en el ciclo. Ver [monitor/property_monitor.py](monitor/property_monitor.py).
  - **Un scrape incompleto NUNCA da de baja nada.** `scrape_profile` marca `self.failed_profiles` cuando `_get_soup` devuelve None (fallo de fetch ≠ listing vacío). Si `failed_profiles` no está vacío, el monitor OMITE toda la detección de bajas ese ciclo. Ver [scraper/idealista_scraper.py](scraper/idealista_scraper.py).
  - **Restaurar borradas sin gastar KIE:** `python -m tools.restore_deleted` (dry-run) → `--apply --limit 10`. Reutiliza `processed_photos` de la BD + cache local `data/photos/<carpeta>/processed/`. Omite las que ya no tienen fotos locales (para esas, ciclo completo).

**15b. El blindaje de `failed_profiles` NO cubría el soft-block (causó borrado masivo a draft).** El guard original solo se activaba ante un fallo DURO (`_get_soup` devuelve None). Pero Scrapfly/DataDome puede devolver `200` + HTML >5000 chars que NO es un captcha reconocido (bloqueo blando o cambio de markup): `_get_soup` devuelve soup válido, `_parse_listing_page` devuelve `[]`, `scrape_profile` lo trataba como "agencia sin más propiedades" y NO marcaba el perfil como fallido. Resultado: todas las propiedades de esa agencia (o de todas, si el bloqueo es general) se pausaban a `draft`. Soluciones:
  - **Página 1 con 0 propiedades = perfil fallido.** Un perfil real SIEMPRE lista inmuebles; 0 en la primera página es soft-block/markup, no agencia vacía. `scrape_profile` ahora marca `fetch_failed` en ese caso. Ver [scraper/idealista_scraper.py](scraper/idealista_scraper.py).
  - **Red de seguridad por umbral en el monitor.** Si las bajas detectadas superan el 50% de las propiedades conocidas, es casi seguro un fallo de scrape (una agencia no da de baja >50% de su stock en 72h) → se OMITE la detección de bajas ese ciclo. Ver [monitor/property_monitor.py](monitor/property_monitor.py).
  - **Reactivar las que quedaron en borrador:** se republican solas en el siguiente ciclo exitoso (`_handle_reappeared`). Para hacerlo al instante sin esperar: `python -m tools.reactivate_paused` (dry-run) → `--apply --limit 10`. Solo cambia `draft → publish` del post existente (no scrapea, no gasta KIE).
