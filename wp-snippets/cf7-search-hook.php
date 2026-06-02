<?php
/**
 * Snippet para functions.php del tema hijo en WordPress.
 *
 * REQUISITOS:
 *   1. Plugin Contact Form 7 activo
 *   2. Plugin Jacobo Agency Manager activo (guarda la URL y secret de la API)
 *   3. El formulario CF7 debe llamarse exactamente "Búsqueda de propiedad"
 *      con los campos: your-message (textarea), your-name, your-email
 *
 * INSTALACIÓN:
 *   Pega este código al final de wp-content/themes/TU-TEMA-HIJO/functions.php
 */

add_action('wpcf7_before_send_mail', 'jacobo_forward_search_to_bot', 10, 1);

function jacobo_forward_search_to_bot(WPCF7_ContactForm $cf7): void {
    if ($cf7->title() !== 'Búsqueda de propiedad') {
        return;
    }
    $submission = WPCF7_Submission::get_instance();
    if (!$submission) {
        return;
    }
    $api_url    = rtrim(get_option('jacobo_api_url', ''), '/');
    $api_secret = get_option('jacobo_api_secret', '');
    if (empty($api_url)) {
        error_log('[jacobo-bot] jacobo_api_url no configurada. Ve a Ajustes > Agencias Colaboradoras.');
        return;
    }
    $data = $submission->get_posted_data();
    // blocking=false → fire-and-forget: CF7 responde al usuario SIN esperar al bot
    wp_remote_post(
        $api_url . '/api/search',
        [
            'timeout'  => 5,
            'blocking' => false,
            'headers'  => [
                'X-API-Secret' => $api_secret,
                'Content-Type' => 'application/json',
            ],
            'body' => wp_json_encode([
                'query' => sanitize_text_field($data['your-message'] ?? ''),
                'name'  => sanitize_text_field($data['your-name']    ?? ''),
                'email' => sanitize_email($data['your-email']        ?? ''),
            ]),
        ]
    );
}
