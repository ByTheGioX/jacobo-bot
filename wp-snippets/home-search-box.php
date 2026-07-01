<?php
/**
 * Cajita de búsqueda con IA para el Home — shortcode [jacobo_search_box]
 *
 * El visitante describe en lenguaje natural lo que busca. Al enviar, WordPress
 * (en el servidor) llama a POST /api/search del bot con la X-API-Secret. El bot
 * busca en el inventario y, si no hay nada, avisa por email a las agencias de la
 * zona. La clave secreta nunca viaja al navegador.
 *
 * REQUISITOS:
 *   - Plugin "Jacobo Agency Manager" activo (guarda jacobo_api_url y jacobo_api_secret).
 *
 * INSTALACIÓN:
 *   1. Pega este código al final de functions.php del tema hijo.
 *   2. Inserta el shortcode [jacobo_search_box] en el Home (bloque de shortcode).
 */

defined('ABSPATH') || exit;

// Guard: si el plugin Jacobo Agency Manager ya registra este shortcode, no lo dupliques.
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
        } else {
            $api_url    = rtrim((string) get_option('jacobo_api_url', ''), '/');
            $api_secret = (string) get_option('jacobo_api_secret', '');
            if ($api_url === '') {
                error_log('[jacobo-bot] jacobo_api_url no configurada (búsqueda Home).');
                $msg = 'No podemos procesar tu búsqueda ahora mismo. Inténtalo más tarde.';
                $msg_ok = false;
            } else {
                // blocking=false → el visitante recibe respuesta al instante; el bot
                // procesa la búsqueda y los emails en segundo plano.
                wp_remote_post($api_url . '/api/search', [
                    'timeout'  => 5,
                    'blocking' => false,
                    'headers'  => [
                        'X-API-Secret' => $api_secret,
                        'Content-Type' => 'application/json',
                    ],
                    'body' => wp_json_encode([
                        'query' => $query,
                        'name'  => $name,
                        'email' => $email,
                    ]),
                ]);
                $msg = '¡Recibido! Estamos buscando y avisando a las agencias de tu zona. '
                     . 'Si dejaste tu email, te contactaremos en cuanto tengamos algo.';
                $msg_ok = true;
            }
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
                        border:1px solid <?= $msg_ok ? '#38b2ac' : '#fc8181' ?>;color:#fff">
                <?= esc_html($msg) ?>
            </div>
        <?php endif; ?>

        <form method="post">
            <?php wp_nonce_field('jacobo_search', 'jacobo_search_nonce'); ?>
            <input type="hidden" name="jacobo_search_submit" value="1">

            <textarea name="query" rows="3" required placeholder="<?= esc_attr($atts['placeholder']) ?>"
                style="width:100%;padding:12px;border:0;border-radius:8px;margin-bottom:12px;
                       font-size:1em;resize:vertical;box-sizing:border-box"></textarea>

            <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px">
                <input name="name" type="text" placeholder="Tu nombre (opcional)"
                    style="flex:1;min-width:160px;padding:11px;border:0;border-radius:8px;box-sizing:border-box">
                <input name="email" type="email" placeholder="Tu email (para avisarte)"
                    style="flex:1;min-width:160px;padding:11px;border:0;border-radius:8px;box-sizing:border-box">
            </div>

            <button type="submit"
                style="background:#f6ad55;color:#1a202c;border:0;padding:13px 32px;border-radius:8px;
                       font-size:1.05em;font-weight:700;cursor:pointer">
                Buscar
            </button>
        </form>
    </div>
    <?php
    return ob_get_clean();
}

} // fin guard function_exists
