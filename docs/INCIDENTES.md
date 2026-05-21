# Incidentes y Soluciones — jacobo-bot

Registro de todos los problemas encontrados durante despliegue y operación, con sus causas raíz y soluciones aplicadas. Sirve de referencia para no repetir los mismos errores.

---

## 1. Caída del servidor WordPress por flood XML-RPC

**Fecha:** 2026-05-20
**Síntoma:** `inmo4you.com` devolvía `ERR_CONNECTION_TIMED_OUT`. Web inaccesible durante ~30 min.
**Causa raíz:** Al correr `python -m tools.diagnose_wp_listing --fix-meta` sin límite, el script hacía una llamada XML-RPC por cada propiedad (50+) en serie y sin delay. Los procesos PHP del hosting compartido CDmon quedaron saturados.
**Solución aplicada:**
- Agregado `time.sleep(8)` entre cada propiedad en `tools/diagnose_wp_listing.py` y `tools/verify_uploaded.py`.
- Default `--limit 0` (procesa todas) pero con pausa generosa.
**Prevención:** Documentado en CLAUDE.md → "Regla crítica: herramientas de diagnóstico en producción". Siempre `--limit 10` primero.

---

## 2. IP del VPS bloqueada por ModSecurity de CDmon

**Síntoma:** Tras varios requests, el VPS recibe `Connection timed out` al WP, pero la web sigue funcionando desde otras IPs.
**Logs del servidor (ejemplo):**
```
ModSecurity: Access denied with code 403 ... [msg "Blocked vulnerability wlwmanifest"]
ModSecurity: Warning. Pattern match "^(\/.+?)?\/+\?author=[0-9]{1,2}$"
```
**Causa raíz:** ModSecurity de CDmon detecta el patrón de muchas llamadas seguidas a `xmlrpc.php` y bloquea la IP temporalmente.
**Solución temporal:** Esperar ~1 hora a que expire el bloqueo, o cambiar de IP (hotspot móvil).
**Solución definitiva:** Pedir a CDmon vía soporte que whiteliste la IP del VPS para xmlrpc.php.

---

## 3. WP_URL en HTTP causa redirects 302 que rompen XML-RPC y subida de fotos

**Síntoma A (XML-RPC):**
```
[ERROR] XML-RPC set_post_meta falló: <ProtocolError for www.inmo4you.com/xmlrpc.php: 302 Found>
```
**Síntoma B (subida de fotos):**
```
[ERROR] Error subiendo media ...: list indices must be integers or slices, not str
```
**Causa raíz:** Si `WP_URL` en `.env` apunta a `http://`, el servidor responde con redirect 302 a `https://`. La librería `xmlrpc.client` no sigue redirects por defecto. Y `requests` al seguir el redirect convierte POST → GET, así que la API de WP devuelve una lista en vez del objeto creado, y `media['id']` falla.
**Solución aplicada:**
- En `wordpress/wp_client.py`: `_url = WP_URL.replace("http://", "https://")` antes de armar `self.base`.
- Agregada clase `_RequestsTransport` (XML-RPC sobre `requests` con `allow_redirects=True`).
**Prevención:** El `.env` siempre debe tener `WP_URL=https://...`. El código ya lo fuerza por seguridad.

---

## 4. Propiedades publicadas no aparecían en /listing-v6-full-width/ (Houzez)

**Síntoma:** Las propiedades nuevas se crean en WP con éxito (visibles en /properties/) pero NO aparecen en el listing público.
**Causa raíz:** El meta `fave_property_status` debe coincidir con el **slug** de la taxonomía `property_status`. En este sitio (idioma español + WPML), los slugs son `en-venta` y `en-alquiler`, NO `for-sale` ni `for-rent`.
**Solución aplicada:** En `wordpress/property_publisher.py`:
```python
status_slug = "en-alquiler" if is_rent else "en-venta"  # antes: "for-rent" / "for-sale"
```
Y se corrieron las 9 propiedades ya existentes con `wp.set_post_meta(pid, {"fave_property_status": "en-venta"})`.
**Verificación:** Consultar `wp-json/wp/v2/property_status` para ver los slugs reales antes de hardcodear.

---

## 5. Cache del servidor con TTL de 48 horas

**Síntoma:** Después de publicar propiedades, no aparecen en la web aunque sí están en la BD de WP.
**Detección:** Headers HTTP muestran `X-Cache: HIT` y `Cache-Control: max-age=172800` (48h).
**Causa raíz:** CDmon (o un plugin de cache) cachea las páginas listing por 48 horas. Los cambios en BD no se reflejan hasta que se purga el cache.
**Solución manual:**
1. WP Admin → Houzez Settings → Tools → Clear Cache
2. Plugin de cache (si existe): "Empty all caches"
3. Panel CDmon → Servidor → Gestionar Caché → Limpiar
4. URL con `?nocache=...` o `?refresh=1` evita el cache para testing
**Verificación rápida:**
```python
requests.get(url + '?nocache=' + str(time.time()))
# Header X-Cache: MISS confirma bypass
```

---

## 6. OpenRouter sin créditos (402) rompe clasificador y reescritor

**Síntoma:**
```
402 Client Error: Payment Required for url: https://openrouter.ai/api/v1/chat/completions
```
**Causa raíz:** OpenRouter cobra por uso. Sin créditos:
- `photo_classifier` no puede clasificar fotos (type/empty/shot) → propiedad queda sin procesar.
- `property_publisher._rewrite_description` falla → usa descripción original (no crítico).
**Solución:** Recargar créditos en https://openrouter.ai/credits
**Prevención:** Monitorear saldo periódicamente. Configurar alerta de saldo bajo en OpenRouter.

---

## 7. Bot no reintentaba propiedades sin `wp_post_id`

**Síntoma:** Tras una caída de WP, propiedades quedan en BD con `wp_post_id = NULL`. Ciclos posteriores reportan "0 nuevas" y no las re-publican.
**Causa raíz:** `_process_and_publish` solo se llama dentro del loop `for prop in scraped_props`, pero el scraper saltea propiedades ya conocidas (`known_ids`), así que `scraped_props` queda vacío.
**Solución aplicada:** Agregado método `_retry_unpublished()` en `monitor/property_monitor.py` que:
1. Lee de BD propiedades activas sin `wp_post_id`.
2. Reconstruye objeto `Property` desde los datos de BD (`_db_prop_to_property`).
3. Corre flujo completo (KIE + WP publish) — el enhancer reutiliza cache local de fotos en `data/photos/<folder>/processed/`, así que no gasta créditos KIE de nuevo.

---

## 8. Subida de fotos crudas (sin procesar por KIE) — riesgo copyright

**Síntoma original:** En un ciclo previo, KIE se quedó sin créditos a mitad de un batch y el código tenía fallback a foto raw → se subieron originales de Idealista a WP. Riesgo de demanda.
**Causa raíz:** `photo_enhancer.py` tenía fallback silencioso: si KIE fallaba, usaba `raw_path` como `local_path`.
**Solución aplicada:**
- Política estricta: si CUALQUIER foto regular falla en KIE → `process_property_photos` devuelve `[]` y la propiedad NO se sube a WP.
- Cero fallback a raw, jamás. Documentado en CLAUDE.md → "Política Anti-Copyright (CRÍTICA)".
- Herramienta `tools/verify_uploaded.py` para auditar propiedades ya publicadas: detecta crudas con pHash + visión IA y con `--reprocess` las arregla.

---

## 9. `set_post_meta` reportaba éxito aunque fallaba

**Síntoma:** Log decía "Fix aplicado" inmediatamente después de "set_post_meta falló".
**Causa raíz:** `WPClient.set_post_meta` capturaba la excepción internamente con `logger.error` pero no la propagaba ni retornaba `False`. El caller asumía éxito siempre.
**Solución aplicada:** `set_post_meta` ahora retorna `bool` y `_fix_status_meta` usa ese valor para decidir si loguear "OK" o "FALLO".

---

## 10. `tools/diagnose_wp_listing` fallaba con 400 al pedir múltiples status

**Síntoma:** `400 Client Error: Bad Request` con `status=publish,pending,draft`.
**Causa raíz:** La REST API de WP con custom post types de Houzez no acepta status separados por coma en una sola query.
**Solución aplicada:** Cambiado a `status=publish` (solo). Es lo único relevante para el listing público.

---

## Checklist rápido para futuras incidencias

Antes de correr cualquier herramienta de mantenimiento contra WordPress en producción:

- [ ] ¿La herramienta tiene `time.sleep(>=1.5s)` entre requests?
- [ ] ¿Se va a correr con `--limit 10` primero?
- [ ] ¿El cliente está avisado de posible lentitud?
- [ ] ¿Se está corriendo en horario de bajo tráfico (madrugada)?
- [ ] ¿La IP desde la que se corre está whitelisted o tiene riesgo de bloqueo?

Después de cualquier cambio en propiedades:

- [ ] Verificar con `?nocache=<timestamp>` que aparece la nueva propiedad.
- [ ] Limpiar cache del hosting / plugin si los cambios no se ven.
- [ ] Confirmar que `fave_property_status` es slug correcto (en-venta / en-alquiler / vacacional), NO label.
