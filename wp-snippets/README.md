# WordPress Snippets

> **✅ Vía recomendada (sin editar functions.php):** el plugin
> `wp-plugin/jacobo-agency-manager/` (v1.1.0+) **ya incluye** los shortcodes
> `[jacobo_search_box]` y `[jacobo_onboarding_form]`. Con subir y activar el plugin
> tienes el panel de admin **y** las dos funciones del front. Ver la guía completa en
> `GUIA_ALTA_AGENCIAS.md` (raíz del repo).
>
> Los archivos `.php` de esta carpeta son la **alternativa** para quien prefiera pegarlos
> en `functions.php` del tema. Llevan guardas `function_exists`, así que no chocan aunque
> el plugin también esté activo.

## cf7-search-hook.php

Conecta el formulario de búsqueda de Contact Form 7 con el bot Python.

### Pasos de instalación

1. Instalar el plugin **Contact Form 7** desde el repositorio de WordPress
2. Activar el plugin **Jacobo Agency Manager** (en `wp-plugin/`)
3. Ir a **Ajustes → Agencias Colaboradoras** y configurar:
   - URL de la API: `http://IP_DEL_VPS:8080`
   - API Secret: (el mismo valor que `FLASK_SECRET` en el .env del VPS)
4. Crear un formulario CF7 con:
   - Título: `Búsqueda de propiedad` (exacto, mayúsculas incluidas)
   - Campos: `[textarea your-message]`, `[text your-name]`, `[email your-email]`
5. Copiar el contenido de `cf7-search-hook.php` al final de `functions.php` del tema hijo
6. Crear una página en WP e insertar el shortcode del formulario CF7

### Firewall (Contabo)

Abrir el puerto **8080 TCP** en el panel de Contabo → Firewall para que WordPress pueda llegar al VPS.

---

## home-search-box.php  (cajita de IA en el Home)

Alternativa autocontenida al formulario de CF7: una "ventana de IA" (estilo costasunsets)
que el visitante usa para describir lo que busca. **No necesita Contact Form 7.**

### Instalación

1. Activar el plugin **Jacobo Agency Manager** y configurar URL + secret de la API.
2. Pegar el contenido de `home-search-box.php` al final del `functions.php` del tema hijo.
3. En el editor del Home, insertar el shortcode `[jacobo_search_box]` (bloque "Shortcode").
   - Opcional: personalizar textos → `[jacobo_search_box titulo="..." subtitulo="..." placeholder="..."]`

El envío llama a `POST /api/search` desde el servidor (la secret nunca llega al navegador).
Si no hay coincidencias en el inventario, el bot avisa por email a las agencias de la zona.

---

## agency-onboarding-form.php  (alta automática de agencias)

Formulario público donde una agencia se da de alta sola. Al enviar, queda **pendiente de
tu aprobación**; cuando la apruebas (Ajustes → Agencias Colaboradoras → "Solicitudes de
alta pendientes"), el bot genera su código corto, la añade a `perfiles.txt`, y scrapea y
publica su perfil automáticamente.

### Instalación

1. Activar el plugin **Jacobo Agency Manager** y configurar URL + secret de la API.
2. Pegar el contenido de `agency-onboarding-form.php` al final del `functions.php` del tema hijo.
3. Crear una página (ej. **"Únete a la red"**) e insertar el shortcode `[jacobo_onboarding_form]`.
4. Esa URL es la que mandas por email a tu listado de agencias. Para enviarla en masa:
   ```
   python -m tools.send_onboarding_invites --link "https://TUWEB.com/unete"            # dry-run
   python -m tools.send_onboarding_invites --link "https://TUWEB.com/unete" --apply     # envía
   ```

### Flujo completo

```
Agencia → página "Únete a la red" → POST /api/onboard → solicitud "pendiente"
Tú → WP Admin → Agencias Colaboradoras → "Aprobar" → POST /api/signups/<id>/approve
   → código generado + perfiles.txt + scrape + publicación automática
```

**Aprobación manual a propósito:** scrapear+publicar gasta créditos KIE; un formulario público
sin filtro sería un vector de abuso. Por eso cada alta espera tu visto bueno.
