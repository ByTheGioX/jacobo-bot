<?php
/**
 * Plugin Name:  Jacobo Agency Manager
 * Description:  Gestiona agencias colaboradoras y altas de Jacobo-Bot, e incluye los shortcodes [jacobo_search_box] (cajita de IA del Home) y [jacobo_onboarding_form] (alta de agencias). Solo subir y activar — sin tocar functions.php.
 * Version:      1.1.0
 * Requires PHP: 7.4
 * Author:       Jacobo-Bot
 */

defined('ABSPATH') || exit;

add_action('admin_menu', function (): void {
    add_options_page(
        'Agencias Colaboradoras',
        'Agencias Colaboradoras',
        'manage_options',
        'jacobo-agencies',
        'jacobo_agencies_render_page'
    );
});

add_action('admin_init', function (): void {
    register_setting('jacobo_bot', 'jacobo_api_url',    ['sanitize_callback' => 'sanitize_url']);
    register_setting('jacobo_bot', 'jacobo_api_secret', ['sanitize_callback' => 'sanitize_text_field']);
});

function jacobo_api_url(): string {
    return rtrim((string) get_option('jacobo_api_url', ''), '/');
}

function jacobo_api_secret(): string {
    return (string) get_option('jacobo_api_secret', '');
}

function jacobo_api_call(string $method, string $path, array $body = []): array {
    if (empty(jacobo_api_url())) {
        return ['error' => 'URL de la API no configurada'];
    }
    $args = [
        'method'  => strtoupper($method),
        'timeout' => 10,
        'headers' => [
            'X-API-Secret' => jacobo_api_secret(),
            'Content-Type' => 'application/json',
        ],
    ];
    if (!empty($body)) {
        $args['body'] = wp_json_encode($body);
    }
    $response = wp_remote_request(jacobo_api_url() . $path, $args);
    if (is_wp_error($response)) {
        return ['error' => $response->get_error_message()];
    }
    $decoded = json_decode(wp_remote_retrieve_body($response), true);
    return is_array($decoded) ? $decoded : [];
}

function jacobo_agencies_render_page(): void {
    if (!current_user_can('manage_options')) {
        wp_die('Sin permisos suficientes.');
    }

    $notice = '';
    $notice_type = 'success';

    if ($_SERVER['REQUEST_METHOD'] === 'POST' && check_admin_referer('jacobo_nonce')) {
        $action = sanitize_key($_POST['jacobo_action'] ?? '');

        if ($action === 'save_settings') {
            update_option('jacobo_api_url',    sanitize_url($_POST['jacobo_api_url'] ?? ''));
            update_option('jacobo_api_secret', sanitize_text_field($_POST['jacobo_api_secret'] ?? ''));
            $notice = '✅ Configuración guardada.';

        } elseif ($action === 'add_agency') {
            $zones_raw = sanitize_text_field($_POST['zones'] ?? '');
            $zones = array_values(array_filter(array_map('trim', explode(',', $zones_raw))));
            $result = jacobo_api_call('POST', '/api/agencies', [
                'name'  => sanitize_text_field($_POST['name']  ?? ''),
                'email' => sanitize_email($_POST['email']      ?? ''),
                'zones' => $zones,
            ]);
            if (isset($result['error'])) {
                $notice = '❌ Error: ' . esc_html($result['error']);
                $notice_type = 'error';
            } else {
                $notice = '✅ Agencia añadida (ID: ' . intval($result['id'] ?? 0) . ').';
            }

        } elseif ($action === 'delete_agency') {
            jacobo_api_call('DELETE', '/api/agencies/' . intval($_POST['agency_id'] ?? 0));
            $notice = '🗑️ Agencia eliminada.';

        } elseif ($action === 'approve_signup') {
            $result = jacobo_api_call('POST', '/api/signups/' . intval($_POST['signup_id'] ?? 0) . '/approve');
            if (isset($result['error'])) {
                $notice = '❌ Error: ' . esc_html($result['error']);
                $notice_type = 'error';
            } else {
                $notice = '✅ Alta aprobada. Código: <strong>' . esc_html($result['code'] ?? '—')
                        . '</strong>. El perfil se está scrapeando y publicando en segundo plano.';
            }

        } elseif ($action === 'reject_signup') {
            jacobo_api_call('POST', '/api/signups/' . intval($_POST['signup_id'] ?? 0) . '/reject');
            $notice = '🗑️ Solicitud rechazada.';
        }
    }

    $signups = jacobo_api_call('GET', '/api/signups?status=pending');
    if (isset($signups['error'])) { $signups = []; }

    $agencies  = jacobo_api_call('GET', '/api/agencies');
    $api_error = $agencies['error'] ?? null;
    if ($api_error) { $agencies = []; }

    $api_url_val    = esc_attr(jacobo_api_url());
    $api_secret_val = esc_attr(jacobo_api_secret());
    ?>
    <div class="wrap">
        <h1>🏠 Agencias Colaboradoras — Jacobo-Bot</h1>

        <?php if ($notice): ?>
        <div class="notice notice-<?= esc_attr($notice_type) ?> is-dismissible"><p><?= wp_kses_post($notice) ?></p></div>
        <?php endif; ?>

        <?php if ($api_error): ?>
        <div class="notice notice-error">
            <p><strong>Error conectando con la API del bot:</strong> <?= esc_html($api_error) ?></p>
            <p>Comprueba que el bot está corriendo en el VPS y que la URL y secret son correctos abajo.</p>
        </div>
        <?php endif; ?>

        <h2>⚙️ Configuración de la API</h2>
        <form method="post">
            <?php wp_nonce_field('jacobo_nonce'); ?>
            <input type="hidden" name="jacobo_action" value="save_settings">
            <table class="form-table">
                <tr>
                    <th><label for="jacobo_api_url">URL del servidor del bot</label></th>
                    <td>
                        <input id="jacobo_api_url" name="jacobo_api_url" type="url" class="regular-text"
                               value="<?= $api_url_val ?>" placeholder="http://IP_DEL_VPS:8080" />
                        <p class="description">Ejemplo: <code>http://123.45.67.89:8080</code></p>
                    </td>
                </tr>
                <tr>
                    <th><label for="jacobo_api_secret">API Secret</label></th>
                    <td>
                        <input id="jacobo_api_secret" name="jacobo_api_secret" type="password"
                               class="regular-text" value="<?= $api_secret_val ?>" />
                        <p class="description">Debe coincidir con <code>FLASK_SECRET</code> en el .env del VPS.</p>
                    </td>
                </tr>
            </table>
            <p class="submit"><input type="submit" class="button button-primary" value="Guardar configuración"></p>
        </form>

        <h2>📨 Solicitudes de alta pendientes <?= !empty($signups) ? '(' . count($signups) . ')' : '' ?></h2>
        <p class="description">Agencias que se han registrado desde la web y esperan tu aprobación.
        Al aprobar se genera su código y se scrapea/publica su perfil automáticamente.</p>
        <?php if (empty($signups)): ?>
            <p style="color:#666">No hay solicitudes pendientes.</p>
        <?php else: ?>
        <table class="wp-list-table widefat fixed striped">
            <thead>
                <tr>
                    <th style="width:40px">ID</th><th>Nombre</th><th>Contacto</th>
                    <th>Perfil Idealista</th><th>Zonas</th><th style="width:140px">Fecha</th>
                    <th style="width:170px">Acción</th>
                </tr>
            </thead>
            <tbody>
            <?php foreach ($signups as $s):
                $zones_raw = $s['zones'] ?? '';
                $decoded   = json_decode((string) $zones_raw, true);
                $zones_str = is_array($decoded) ? implode(', ', $decoded) : (string) $zones_raw;
                $zones_display = $zones_str !== '' ? esc_html($zones_str) : '<em style="color:#999">Todas</em>';
                $contact = trim(($s['contact_email'] ?? '') . ' ' . ($s['phone'] ?? ''));
            ?>
            <tr>
                <td><?= intval($s['id']) ?></td>
                <td><strong><?= esc_html($s['name']) ?></strong></td>
                <td><?= esc_html($contact !== '' ? $contact : '—') ?></td>
                <td><a href="<?= esc_url($s['idealista_url']) ?>" target="_blank" rel="noopener">ver perfil ↗</a></td>
                <td><?= wp_kses_post($zones_display) ?></td>
                <td><?= esc_html(substr((string) ($s['created_at'] ?? ''), 0, 16)) ?></td>
                <td>
                    <form method="post" style="display:inline"
                          onsubmit="return confirm('¿Aprobar y scrapear «<?= esc_js($s['name']) ?>»? Esto gasta créditos KIE.')">
                        <?php wp_nonce_field('jacobo_nonce'); ?>
                        <input type="hidden" name="jacobo_action" value="approve_signup">
                        <input type="hidden" name="signup_id" value="<?= intval($s['id']) ?>">
                        <button type="submit" class="button button-primary button-small">Aprobar</button>
                    </form>
                    <form method="post" style="display:inline"
                          onsubmit="return confirm('¿Rechazar «<?= esc_js($s['name']) ?>»?')">
                        <?php wp_nonce_field('jacobo_nonce'); ?>
                        <input type="hidden" name="jacobo_action" value="reject_signup">
                        <input type="hidden" name="signup_id" value="<?= intval($s['id']) ?>">
                        <button type="submit" class="button button-small button-link-delete">Rechazar</button>
                    </form>
                </td>
            </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
        <?php endif; ?>

        <h2>📋 Agencias registradas</h2>
        <?php if (empty($agencies) && !$api_error): ?>
            <p style="color:#666">No hay agencias registradas todavía. Añade la primera abajo.</p>
        <?php elseif (!empty($agencies)): ?>
        <table class="wp-list-table widefat fixed striped">
            <thead>
                <tr><th style="width:40px">ID</th><th>Nombre</th><th>Email</th><th>Zonas</th><th style="width:60px">Activa</th><th style="width:80px">Acción</th></tr>
            </thead>
            <tbody>
            <?php foreach ($agencies as $ag):
                $zones_raw = $ag['zones'] ?? '';
                if (is_array($zones_raw)) {
                    $zones_str = implode(', ', $zones_raw);
                } else {
                    $decoded = json_decode($zones_raw, true);
                    $zones_str = is_array($decoded) ? implode(', ', $decoded) : $zones_raw;
                }
                $zones_display = $zones_str ?: '<em style="color:#999">Todas las zonas</em>';
            ?>
            <tr>
                <td><?= intval($ag['id']) ?></td>
                <td><?= esc_html($ag['name']) ?></td>
                <td><?= esc_html($ag['email']) ?></td>
                <td><?= wp_kses_post($zones_display) ?></td>
                <td><?= $ag['active'] ? '✅' : '❌' ?></td>
                <td>
                    <form method="post" style="display:inline"
                          onsubmit="return confirm('¿Eliminar la agencia <?= esc_js($ag['name']) ?>?')">
                        <?php wp_nonce_field('jacobo_nonce'); ?>
                        <input type="hidden" name="jacobo_action" value="delete_agency">
                        <input type="hidden" name="agency_id" value="<?= intval($ag['id']) ?>">
                        <button type="submit" class="button button-small button-link-delete">Eliminar</button>
                    </form>
                </td>
            </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
        <?php endif; ?>

        <h2>➕ Añadir agencia nueva</h2>
        <form method="post">
            <?php wp_nonce_field('jacobo_nonce'); ?>
            <input type="hidden" name="jacobo_action" value="add_agency">
            <table class="form-table">
                <tr>
                    <th><label for="agency_name">Nombre</label></th>
                    <td><input id="agency_name" name="name" type="text" class="regular-text" required></td>
                </tr>
                <tr>
                    <th><label for="agency_email">Email</label></th>
                    <td><input id="agency_email" name="email" type="email" class="regular-text" required></td>
                </tr>
                <tr>
                    <th><label for="agency_zones">Zonas</label></th>
                    <td>
                        <input id="agency_zones" name="zones" type="text" class="regular-text"
                               placeholder="malaga, marbella, torremolinos">
                        <p class="description">Separadas por coma. <strong>Vacío</strong> = recibe todas las búsquedas.</p>
                    </td>
                </tr>
            </table>
            <p class="submit"><input type="submit" class="button button-primary" value="Añadir agencia"></p>
        </form>
    </div>
    <?php
}


// ─────────────────────────────────────────────────────────────
//  Shortcodes públicos (front-end)
//  Incluidos en el plugin para no tener que editar functions.php.
//  Guardados con function_exists por si además existe el snippet suelto.
// ─────────────────────────────────────────────────────────────

if (!function_exists('jacobo_onboarding_form_render')) {
    add_shortcode('jacobo_onboarding_form', 'jacobo_onboarding_form_render');

    function jacobo_onboarding_form_render(): string {
        $msg = '';
        $msg_ok = true;

        if (($_POST['jacobo_onboard_submit'] ?? '') === '1'
            && isset($_POST['jacobo_onboard_nonce'])
            && wp_verify_nonce($_POST['jacobo_onboard_nonce'], 'jacobo_onboard')) {

            $name  = sanitize_text_field($_POST['name']  ?? '');
            $email = sanitize_email($_POST['email']      ?? '');
            $phone = sanitize_text_field($_POST['phone'] ?? '');
            $url   = esc_url_raw($_POST['idealista_url'] ?? '');
            $zones = sanitize_text_field($_POST['zones'] ?? '');

            if ($name === '' || $url === '') {
                $msg = 'Por favor, indica al menos el nombre y la URL de tu perfil de Idealista.';
                $msg_ok = false;
            } elseif (jacobo_api_url() === '') {
                error_log('[jacobo-bot] jacobo_api_url no configurada (alta de agencia).');
                $msg = 'No podemos procesar el alta ahora mismo. Inténtalo más tarde.';
                $msg_ok = false;
            } else {
                $resp = wp_remote_post(jacobo_api_url() . '/api/onboard', [
                    'timeout' => 15,
                    'headers' => [
                        'X-API-Secret' => jacobo_api_secret(),
                        'Content-Type' => 'application/json',
                    ],
                    'body' => wp_json_encode([
                        'name'          => $name,
                        'email'         => $email,
                        'phone'         => $phone,
                        'idealista_url' => $url,
                        'zones'         => $zones,
                    ]),
                ]);
                if (is_wp_error($resp)) {
                    $msg = 'No hemos podido enviar tu solicitud. Inténtalo de nuevo en unos minutos.';
                    $msg_ok = false;
                } else {
                    $code = wp_remote_retrieve_response_code($resp);
                    $body = json_decode(wp_remote_retrieve_body($resp), true);
                    if ($code === 201) {
                        $msg = '¡Gracias! Hemos recibido tu solicitud. La revisaremos y, una vez aprobada, '
                             . 'tus propiedades aparecerán automáticamente en la web.';
                    } elseif ($code === 200 && ($body['status'] ?? '') === 'duplicate') {
                        $msg = 'Tu perfil ya estaba registrado o pendiente de revisión. No hace falta enviarlo otra vez.';
                    } else {
                        $msg = esc_html($body['error'] ?? 'No hemos podido procesar tu solicitud. Revisa la URL de Idealista.');
                        $msg_ok = false;
                    }
                }
            }
        }

        ob_start();
        ?>
        <div class="jacobo-onboard" style="max-width:560px;margin:0 auto;font-family:inherit">
            <?php if ($msg !== ''): ?>
                <div style="padding:14px 18px;border-radius:8px;margin-bottom:18px;
                            background:<?= $msg_ok ? '#e6fffa' : '#fff5f5' ?>;
                            border:1px solid <?= $msg_ok ? '#38b2ac' : '#fc8181' ?>;
                            color:<?= $msg_ok ? '#234e52' : '#822727' ?>"><?= $msg ?></div>
            <?php endif; ?>
            <?php if (!($msg_ok && $msg !== '')): ?>
            <form method="post">
                <?php wp_nonce_field('jacobo_onboard', 'jacobo_onboard_nonce'); ?>
                <input type="hidden" name="jacobo_onboard_submit" value="1">
                <label style="display:block;margin:0 0 4px;font-weight:600">Nombre de la inmobiliaria *</label>
                <input name="name" type="text" required style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">
                <label style="display:block;margin:0 0 4px;font-weight:600">Email de contacto</label>
                <input name="email" type="email" style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">
                <label style="display:block;margin:0 0 4px;font-weight:600">Teléfono</label>
                <input name="phone" type="text" style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">
                <label style="display:block;margin:0 0 4px;font-weight:600">URL de tu perfil de Idealista *</label>
                <input name="idealista_url" type="url" required placeholder="https://www.idealista.com/pro/tu-agencia/" style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">
                <label style="display:block;margin:0 0 4px;font-weight:600">Zonas donde operas</label>
                <input name="zones" type="text" placeholder="Málaga, Marbella, Torremolinos" style="width:100%;padding:10px;margin-bottom:6px;border:1px solid #cbd5e0;border-radius:6px">
                <p style="font-size:.85em;color:#718096;margin:0 0 18px">Separadas por coma o códigos postales.</p>
                <button type="submit" style="background:#2c5282;color:#fff;border:0;padding:12px 28px;border-radius:6px;font-size:1em;font-weight:600;cursor:pointer">Unirme a la red</button>
            </form>
            <?php endif; ?>
        </div>
        <?php
        return ob_get_clean();
    }
}

if (!function_exists('jacobo_search_box_render')) {
    add_shortcode('jacobo_search_box', 'jacobo_search_box_render');

    function jacobo_search_box_render(array $atts = []): string {
        $atts = shortcode_atts([
            'titulo'      => '¿No encuentras lo que buscas?',
            'subtitulo'   => 'Descríbelo y lo buscamos por ti entre nuestras agencias colaboradoras.',
            'placeholder' => 'Ej: piso de 2 habitaciones en Málaga, con terraza, hasta 200.000 €',
        ], $atts);

        $msg = '';
        $msg_ok = true;

        if (($_POST['jacobo_search_submit'] ?? '') === '1'
            && isset($_POST['jacobo_search_nonce'])
            && wp_verify_nonce($_POST['jacobo_search_nonce'], 'jacobo_search')) {

            $query = sanitize_textarea_field($_POST['query'] ?? '');
            $name  = sanitize_text_field($_POST['name']      ?? '');
            $email = sanitize_email($_POST['email']          ?? '');

            if ($query === '') {
                $msg = 'Escribe lo que buscas para poder ayudarte.';
                $msg_ok = false;
            } elseif (jacobo_api_url() === '') {
                error_log('[jacobo-bot] jacobo_api_url no configurada (búsqueda Home).');
                $msg = 'No podemos procesar tu búsqueda ahora mismo. Inténtalo más tarde.';
                $msg_ok = false;
            } else {
                wp_remote_post(jacobo_api_url() . '/api/search', [
                    'timeout'  => 5,
                    'blocking' => false,
                    'headers'  => [
                        'X-API-Secret' => jacobo_api_secret(),
                        'Content-Type' => 'application/json',
                    ],
                    'body' => wp_json_encode(['query' => $query, 'name' => $name, 'email' => $email]),
                ]);
                $msg = '¡Recibido! Estamos buscando y avisando a las agencias de tu zona. '
                     . 'Si dejaste tu email, te contactaremos en cuanto tengamos algo.';
            }
        }

        ob_start();
        ?>
        <div class="jacobo-search" style="max-width:640px;margin:0 auto;padding:28px;
             background:linear-gradient(135deg,#1a365d,#2c5282);border-radius:14px;color:#fff;font-family:inherit">
            <h3 style="margin:0 0 6px;font-size:1.5em;color:#fff"><?= esc_html($atts['titulo']) ?></h3>
            <p style="margin:0 0 18px;opacity:.85"><?= esc_html($atts['subtitulo']) ?></p>
            <?php if ($msg !== ''): ?>
                <div style="padding:14px 18px;border-radius:8px;margin-bottom:16px;
                            background:<?= $msg_ok ? 'rgba(56,178,172,.2)' : 'rgba(252,129,129,.2)' ?>;
                            border:1px solid <?= $msg_ok ? '#38b2ac' : '#fc8181' ?>;color:#fff"><?= esc_html($msg) ?></div>
            <?php endif; ?>
            <form method="post">
                <?php wp_nonce_field('jacobo_search', 'jacobo_search_nonce'); ?>
                <input type="hidden" name="jacobo_search_submit" value="1">
                <textarea name="query" rows="3" required placeholder="<?= esc_attr($atts['placeholder']) ?>"
                    style="width:100%;padding:12px;border:0;border-radius:8px;margin-bottom:12px;font-size:1em;resize:vertical;box-sizing:border-box"></textarea>
                <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
                    <input name="name" type="text" placeholder="Tu nombre (opcional)" style="flex:1;min-width:160px;padding:11px;border:0;border-radius:8px;box-sizing:border-box">
                    <input name="email" type="email" placeholder="Tu email (para avisarte)" style="flex:1;min-width:160px;padding:11px;border:0;border-radius:8px;box-sizing:border-box">
                </div>
                <button type="submit" style="background:#f6ad55;color:#1a202c;border:0;padding:13px 32px;border-radius:8px;font-size:1.05em;font-weight:700;cursor:pointer">Buscar</button>
            </form>
        </div>
        <?php
        return ob_get_clean();
    }
}
