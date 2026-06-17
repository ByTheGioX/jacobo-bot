<?php
/**
 * Plugin Name:  Jacobo Agency Manager
 * Description:  Gestiona las agencias colaboradoras de Jacobo-Bot desde el admin de WordPress.
 * Version:      1.0.0
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
