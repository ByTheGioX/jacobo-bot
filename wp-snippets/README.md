# WordPress Snippets

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
