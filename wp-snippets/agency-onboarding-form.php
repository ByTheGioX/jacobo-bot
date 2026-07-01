<?php
/**
 * Formulario público de alta de agencias — shortcode [jacobo_onboarding_form]
 *
 * La agencia rellena sus datos + su perfil de Idealista. Al enviar, WordPress
 * (en el servidor) llama a POST /api/onboard del bot con la X-API-Secret, así la
 * clave nunca viaja al navegador. El alta queda "pendiente" hasta que la apruebes
 * desde Ajustes → Agencias Colaboradoras.
 *
 * REQUISITOS:
 *   - Plugin "Jacobo Agency Manager" activo (guarda jacobo_api_url y jacobo_api_secret).
 *
 * INSTALACIÓN:
 *   1. Pega este código al final de functions.php del tema hijo.
 *   2. Crea una página (ej. "Únete a la red") e inserta el shortcode [jacobo_onboarding_form].
 *   3. Esa es la URL que mandas por email a tu listado de agencias.
 */

defined('ABSPATH') || exit;

// Guard: si el plugin Jacobo Agency Manager ya registra este shortcode, no lo dupliques.
if (!function_exists('jacobo_onboarding_form_render')) {

add_shortcode('jacobo_onboarding_form', 'jacobo_onboarding_form_render');

function jacobo_onboarding_form_render(): string {
    $msg = '';
    $msg_ok = true;

    if (($_POST['jacobo_onboard_submit'] ?? '') === '1'
        && isset($_POST['jacobo_onboard_nonce'])
        && wp_verify_nonce($_POST['jacobo_onboard_nonce'], 'jacobo_onboard')) {

        $name  = sanitize_text_field($_POST['name']          ?? '');
        $email = sanitize_email($_POST['email']              ?? '');
        $phone = sanitize_text_field($_POST['phone']         ?? '');
        $url   = esc_url_raw($_POST['idealista_url']         ?? '');
        $zones = sanitize_text_field($_POST['zones']         ?? '');

        if ($name === '' || $url === '') {
            $msg = 'Por favor, indica al menos el nombre y la URL de tu perfil de Idealista.';
            $msg_ok = false;
        } else {
            $api_url    = rtrim((string) get_option('jacobo_api_url', ''), '/');
            $api_secret = (string) get_option('jacobo_api_secret', '');
            if ($api_url === '') {
                error_log('[jacobo-bot] jacobo_api_url no configurada (alta de agencia).');
                $msg = 'No podemos procesar el alta ahora mismo. Inténtalo más tarde.';
                $msg_ok = false;
            } else {
                $resp = wp_remote_post($api_url . '/api/onboard', [
                    'timeout' => 15,
                    'headers' => [
                        'X-API-Secret' => $api_secret,
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
                        $msg_ok = true;
                    } elseif ($code === 200 && ($body['status'] ?? '') === 'duplicate') {
                        $msg = 'Tu perfil ya estaba registrado o pendiente de revisión. No hace falta enviarlo otra vez.';
                        $msg_ok = true;
                    } else {
                        $msg = esc_html($body['error'] ?? 'No hemos podido procesar tu solicitud. Revisa la URL de Idealista.');
                        $msg_ok = false;
                    }
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
                        color:<?= $msg_ok ? '#234e52' : '#822727' ?>">
                <?= $msg ?>
            </div>
        <?php endif; ?>

        <?php if (!($msg_ok && $msg !== '')): ?>
        <form method="post" class="jacobo-onboard-form">
            <?php wp_nonce_field('jacobo_onboard', 'jacobo_onboard_nonce'); ?>
            <input type="hidden" name="jacobo_onboard_submit" value="1">

            <label style="display:block;margin:0 0 4px;font-weight:600">Nombre de la inmobiliaria *</label>
            <input name="name" type="text" required
                   style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">

            <label style="display:block;margin:0 0 4px;font-weight:600">Email de contacto</label>
            <input name="email" type="email"
                   style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">

            <label style="display:block;margin:0 0 4px;font-weight:600">Teléfono</label>
            <input name="phone" type="text"
                   style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">

            <label style="display:block;margin:0 0 4px;font-weight:600">URL de tu perfil de Idealista *</label>
            <input name="idealista_url" type="url" required placeholder="https://www.idealista.com/pro/tu-agencia/"
                   style="width:100%;padding:10px;margin-bottom:14px;border:1px solid #cbd5e0;border-radius:6px">

            <label style="display:block;margin:0 0 4px;font-weight:600">Zonas donde operas</label>
            <input name="zones" type="text" placeholder="Málaga, Marbella, Torremolinos"
                   style="width:100%;padding:10px;margin-bottom:6px;border:1px solid #cbd5e0;border-radius:6px">
            <p style="font-size:.85em;color:#718096;margin:0 0 18px">Separadas por coma o códigos postales.</p>

            <button type="submit"
                    style="background:#2c5282;color:#fff;border:0;padding:12px 28px;border-radius:6px;
                           font-size:1em;font-weight:600;cursor:pointer">
                Unirme a la red
            </button>
        </form>
        <?php endif; ?>
    </div>
    <?php
    return ob_get_clean();
}

} // fin guard function_exists
