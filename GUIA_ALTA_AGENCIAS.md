# Guía de puesta en producción — Alta de agencias + Cajita de IA

Guía sin tecnicismos para dejar funcionando las dos funciones nuevas:
1. **Cajita de IA en el Home** — el visitante escribe lo que busca.
2. **Alta de agencias** — las inmobiliarias se registran solas y, tras tu aprobación,
   sus pisos se publican automáticamente.

Todo el código ya está subido. Faltan estos pasos manuales (una sola vez).

---

## Paso 1 — Actualizar el bot en el servidor (VPS)

En el VPS, abrir la carpeta del bot y ejecutar:

```
cd C:\Users\LIVETEAM\Desktop\jacobo-bot && git pull
```

Luego arrancar el bot. Tienes dos modos:

- **Solo web (recomendado si NO quieres que se dispare el scraping automático):**
  ```
  python main.py --serve
  ```
  Levanta el formulario de alta, el buscador y el dashboard. Las altas que apruebes
  sí scrapean ese perfil concreto bajo demanda, pero no hay ciclo automático de 72h.

- **Completo (web + scraping automático cada 72h):**
  ```
  python main.py
  ```

En ambos, en los logs debe aparecer una línea con:
```
[API] ... en http://0.0.0.0:8080
```

> Si el firewall del VPS (Contabo) no tiene abierto el puerto **8080 TCP**, ábrelo:
> Panel de Contabo → Firewall → añadir regla TCP puerto 8080.

---

## Paso 2 — Instalar el plugin en WordPress (1 solo archivo)

El plugin ya trae **todo**: el panel de administración, el formulario de alta y la
cajita de búsqueda. **No hay que tocar `functions.php`.**

1. Comprimir la carpeta `wp-plugin/jacobo-agency-manager/` en un `.zip`.
   (o subirla por FTP a `wp-content/plugins/`).
2. En WordPress: **Plugins → Añadir nuevo → Subir plugin** → elegir el `.zip` → **Instalar** → **Activar**.
   - Si ya lo tenías instalado, simplemente sube la carpeta nueva encima (versión 1.1.0).
3. Ir a **Ajustes → Agencias Colaboradoras** y rellenar:
   - **URL del servidor del bot**: `http://IP_DEL_VPS:8080`
   - **API Secret**: el mismo valor que `FLASK_SECRET` en el `.env` del VPS.
   - Guardar. Si la conexión es correcta, verás la lista de agencias (aunque esté vacía).

---

## Paso 3 — Poner la cajita de búsqueda en el Home

1. Editar la página de Inicio con el editor de WordPress.
2. Añadir un bloque **"Shortcode"** donde quieras la cajita.
3. Escribir dentro:
   ```
   [jacobo_search_box]
   ```
4. Guardar. Ya aparece la ventana "¿No encuentras lo que buscas?".

Opcional, personalizar textos:
```
[jacobo_search_box titulo="Cuéntanos qué buscas" subtitulo="Nosotros lo encontramos" placeholder="Ej: chalet en Marbella con piscina"]
```

---

## Paso 4 — Crear la página de alta de agencias

1. **Páginas → Añadir nueva**. Título: por ejemplo **"Únete a la red"**.
2. Añadir un bloque **"Shortcode"** y escribir:
   ```
   [jacobo_onboarding_form]
   ```
3. Publicar. La URL de esa página (ej. `https://tuweb.com/unete`) es la que mandarás
   a las agencias.

---

## Paso 5 — Invitar a las agencias por email

Desde el VPS, con la web ya lista:

```
# Primero en seco (solo muestra a quién enviaría, NO envía):
python -m tools.send_onboarding_invites --link "https://tuweb.com/unete"

# Cuando estés conforme, enviar de verdad:
python -m tools.send_onboarding_invites --link "https://tuweb.com/unete" --apply

# Por tandas (recomendado la primera vez):
python -m tools.send_onboarding_invites --link "https://tuweb.com/unete" --apply --limit 10
```

Por defecto usa las agencias ya guardadas en la base de datos. Para enviar a un listado
propio, pasar un CSV con columnas `nombre,email`:
```
python -m tools.send_onboarding_invites --link "https://tuweb.com/unete" --csv agencias.csv --apply
```

---

## Paso 6 — Aprobar las altas que lleguen

Cada vez que una agencia se registra, te aparece en **Ajustes → Agencias Colaboradoras →
"Solicitudes de alta pendientes"**.

- **Aprobar** → el bot genera su código, la añade y **scrapea y publica sus pisos** (esto
  gasta créditos de mejora de fotos, por eso es manual).
- **Rechazar** → se descarta.

---

## Cómo funciona por dentro (resumen)

```
Agencia → página "Únete a la red"  →  el bot guarda la solicitud como "pendiente"
Tú → panel de WordPress → "Aprobar"  →  código + scrape + publicación automática
Visitante → cajita del Home → el bot busca; si no hay nada, avisa a las agencias de la zona
```

**Nota importante (solo servidor):** la lista de perfiles vive en el archivo
`configuracion/perfiles.txt` del VPS. Las altas aprobadas lo actualizan solas allí.
Ese archivo no se sube a GitHub, así que nunca se pierde con un `git pull`.

---

## Texto sugerido para el email de invitación

> **Asunto:** Únete a nuestra red de inmobiliarias
>
> Hola [nombre],
>
> Estamos ampliando nuestra red de inmobiliarias colaboradoras. Al unirte, tus
> propiedades se publican automáticamente en nuestra web y recibes las búsquedas de
> compradores de tu zona, sin que tengas que hacer nada manualmente.
>
> Solo tienes que rellenar tus datos y tu perfil de Idealista aquí:
> 👉 https://tuweb.com/unete
>
> Un saludo,
> [Tu agencia]

*(El script `send_onboarding_invites.py` ya envía un email con este contenido y el botón; este texto es por si prefieres mandarlo a mano.)*
