"""
Flask API server para jacobo-bot.
Endpoints:
  POST /api/search           — búsqueda de comprador (async, responde 202 inmediato)
  GET  /dashboard            — HTML con estadísticas (?key=DASHBOARD_PASSWORD)
  GET  /api/agencies         — lista agencias
  POST /api/agencies         — añade agencia
  DELETE /api/agencies/<id>  — elimina agencia
Todos los endpoints excepto /dashboard requieren X-API-Secret header (o ?secret=).
"""

import html
import json
import logging
import threading
from flask import Flask, abort, jsonify, request, Response

from onboarding.registry import validate_idealista_profile_url, register_agency_profile

logger = logging.getLogger(__name__)

_SECRET: str = ""
_DASHBOARD_PASSWORD: str = ""


def create_app(secret: str = "", dashboard_password: str = "") -> Flask:
    global _SECRET, _DASHBOARD_PASSWORD
    _SECRET = secret
    _DASHBOARD_PASSWORD = dashboard_password

    app = Flask(__name__)
    app.logger.setLevel(logging.WARNING)

    @app.route("/api/search", methods=["POST"])
    def search():
        _require_secret()
        data = request.get_json(force=True, silent=True) or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "query requerida"}), 400
        name = data.get("name", "")
        email = data.get("email", "")
        # sync=true (cajita del Home): busca YA y devuelve los resultados para
        # mostrarlos al visitante; los emails a agencias siguen en background.
        if data.get("sync"):
            try:
                return jsonify(_process_search_sync(query, name, email)), 200
            except Exception:
                logger.exception("Sync search falló, degradando a async: '%s'", query[:50])
                threading.Thread(
                    target=_process_search_async, args=(query, name, email), daemon=True,
                ).start()
                return jsonify({"status": "received", "fallback": True}), 202
        # Legacy (CF7 fire-and-forget): responde al instante, todo en background.
        threading.Thread(
            target=_process_search_async,
            args=(query, name, email),
            daemon=True,
        ).start()
        return jsonify({"status": "received"}), 202

    @app.route("/dashboard")
    def dashboard():
        key = request.args.get("key", "")
        if _DASHBOARD_PASSWORD and key != _DASHBOARD_PASSWORD:
            abort(401)
        from database.db import Database
        db = Database()
        stats = db.get_dashboard_stats()
        searches = db.get_recent_searches(limit=10)
        return Response(
            _render_dashboard_html(stats, searches),
            content_type="text/html; charset=utf-8",
        )

    @app.route("/api/agencies", methods=["GET"])
    def list_agencies():
        _require_secret()
        from database.db import Database
        return jsonify(Database().get_all_agencies())

    @app.route("/api/agencies", methods=["POST"])
    def add_agency():
        _require_secret()
        data = request.get_json(force=True, silent=True) or {}
        name  = (data.get("name")  or "").strip()
        email = (data.get("email") or "").strip()
        zones = data.get("zones") or []
        if not name or not email:
            return jsonify({"error": "name y email son obligatorios"}), 400
        from database.db import Database
        agency_id = Database().add_agency(name, email, zones if isinstance(zones, list) else [])
        return jsonify({"id": agency_id, "name": name, "email": email}), 201

    @app.route("/api/agencies/<int:agency_id>", methods=["DELETE"])
    def delete_agency(agency_id: int):
        _require_secret()
        from database.db import Database
        Database().delete_agency(agency_id)
        return jsonify({"deleted": agency_id})

    # ── Alta automática de agencias ──────────────────────────────
    @app.route("/api/onboard", methods=["POST"])
    def onboard():
        """Recibe una solicitud de alta desde el formulario público de WordPress.
        Guarda como 'pending' — NO scrapea hasta que el admin apruebe."""
        _require_secret()
        data = request.get_json(force=True, silent=True) or {}
        # Caps de longitud: el formulario es público, evita filas gigantes en la BD.
        name = (data.get("name") or "").strip()[:200]
        url  = (data.get("idealista_url") or "").strip()[:500]
        if not name:
            return jsonify({"error": "nombre requerido"}), 400
        if not validate_idealista_profile_url(url):
            return jsonify({"error": "URL de perfil de Idealista no válida (debe ser idealista.com/pro/...)"}), 400
        zones = data.get("zones") or []
        if isinstance(zones, str):
            zones = [z.strip() for z in zones.split(",") if z.strip()]
        zones = [str(z)[:80] for z in zones if str(z).strip()][:20] if isinstance(zones, list) else []
        from database.db import Database
        db = Database()
        if db.signup_url_exists(url):
            return jsonify({"status": "duplicate", "message": "Ese perfil ya está registrado o pendiente"}), 200
        signup_id = db.add_signup(
            name=name,
            idealista_url=url,
            contact_email=(data.get("email") or "").strip()[:200],
            phone=(data.get("phone") or "").strip()[:50],
            zones=zones,
        )
        logger.info("Nueva solicitud de alta #%s: %s (%s)", signup_id, name, url)
        return jsonify({"status": "received", "id": signup_id}), 201

    @app.route("/api/signups", methods=["GET"])
    def list_signups():
        _require_secret()
        from database.db import Database
        status = request.args.get("status") or None
        return jsonify(Database().list_signups(status))

    @app.route("/api/signups/<int:signup_id>/approve", methods=["POST"])
    def approve_signup(signup_id: int):
        _require_secret()
        from database.db import Database
        db = Database()
        signup = db.get_signup(signup_id)
        if not signup:
            return jsonify({"error": "solicitud no encontrada"}), 404
        if signup["status"] == "approved":
            return jsonify({"status": "already_approved", "code": signup.get("agency_code")}), 200
        url = signup["idealista_url"]
        if not validate_idealista_profile_url(url):
            return jsonify({"error": "la URL guardada no es válida"}), 400
        try:
            zones = json.loads(signup.get("zones") or "[]")
        except Exception:
            zones = []
        try:
            code = register_agency_profile(signup["name"], url)
        except Exception:
            logger.exception("Error registrando el perfil de la solicitud #%s", signup_id)
            return jsonify({"error": "no se pudo registrar el perfil (revisa los logs del servidor)"}), 500
        db.set_signup_status(signup_id, "approved", agency_code=code)
        # También se registra como agencia colaboradora (para recibir búsquedas por zona)
        if signup.get("contact_email"):
            db.add_agency(signup["name"], signup["contact_email"], zones)
        threading.Thread(target=_scrape_single_async, args=(url,), daemon=True).start()
        logger.info("Alta #%s aprobada → código %s, scrape en marcha", signup_id, code)
        return jsonify({"status": "approved", "code": code}), 202

    @app.route("/api/signups/<int:signup_id>/reject", methods=["POST"])
    def reject_signup(signup_id: int):
        _require_secret()
        from database.db import Database
        db = Database()
        if not db.get_signup(signup_id):
            return jsonify({"error": "solicitud no encontrada"}), 404
        db.set_signup_status(signup_id, "rejected")
        return jsonify({"status": "rejected", "id": signup_id})

    return app


def _require_secret():
    if not _SECRET:
        return
    provided = (
        request.headers.get("X-API-Secret")
        or request.args.get("secret")
        or (request.get_json(force=True, silent=True) or {}).get("secret")
    )
    if provided != _SECRET:
        abort(401)


def _send_agency_emails_async(query: str, name: str, email: str, criteria_dict: dict, search_id: int):
    """Envía los emails a agencias en background (SMTP es lento; no bloquear al visitante)."""
    try:
        from search.smart_search import SearchCriteria
        from search.email_sender import AgencyEmailSender

        c = criteria_dict
        criteria = SearchCriteria(
            raw_query=query,
            contact_email=email,
            contact_name=name,
            location=c.get("location", ""),
            zones=c.get("zones") or [],
            property_type=c.get("property_type", ""),
            operation=c.get("operation", "sale"),
            rooms_min=c.get("rooms_min"),
            rooms_max=c.get("rooms_max"),
            price_max=c.get("price_max"),
            area_min=c.get("area_min"),
            has_parking=bool(c.get("has_parking")),
            has_pool=bool(c.get("has_pool")),
            has_terrace=bool(c.get("has_terrace")),
        )
        AgencyEmailSender().send_to_agencies(criteria, search_id)
        logger.info("Búsqueda '%s' reenviada a agencias (search_id=%s)", query[:50], search_id)
    except Exception:
        logger.exception("Error enviando emails de búsqueda: '%s'", query[:50])


def _process_search_sync(query: str, name: str, email: str) -> dict:
    """Busca inline y devuelve los matches publicados (para pintarlos en la web).
    Si no hay resultados, dispara los emails a agencias en background."""
    try:
        from search.smart_search import SmartSearch

        searcher = SmartSearch()
        result = searcher.process_query(query, contact_email=email, contact_name=name)

        if result["needs_agency_email"]:
            threading.Thread(
                target=_send_agency_emails_async,
                args=(query, name, email, result["criteria"], result["search_id"]),
                daemon=True,
            ).start()

        # Solo se muestran al visitante las propiedades publicadas en WP.
        visible = [
            {
                "wp_post_id": m["wp_post_id"],
                "title": m.get("title") or "",
                "price": m.get("price"),
                "location": m.get("location") or "",
                "rooms": m.get("rooms"),
            }
            for m in result["matches"] if m.get("wp_post_id")
        ]
        logger.info("Búsqueda sync '%s' → %d matches (%d visibles)",
                    query[:50], len(result["matches"]), len(visible))
        return {
            "matches": visible[:12],
            "total": len(visible),
            "forwarded_to_agencies": bool(result["needs_agency_email"]),
        }
    except Exception:
        logger.exception("Error en búsqueda sync: '%s'", query[:50])
        return {"matches": [], "total": 0, "forwarded_to_agencies": False, "error": "internal"}


def _process_search_async(query: str, name: str, email: str):
    try:
        from search.smart_search import SmartSearch

        searcher = SmartSearch()
        result = searcher.process_query(query, contact_email=email, contact_name=name)

        if result["needs_agency_email"]:
            _send_agency_emails_async(query, name, email, result["criteria"], result["search_id"])
        else:
            logger.info("Búsqueda '%s' → %d coincidencias en DB", query[:50], len(result["matches"]))
    except Exception:
        logger.exception("Error procesando búsqueda async: '%s'", query[:50])


def _scrape_single_async(profile_url: str):
    """Scrapea y publica el perfil recién aprobado, en background (no bloquea la API)."""
    try:
        from monitor.property_monitor import PropertyMonitor
        stats = PropertyMonitor().run_single_profile(profile_url)
        logger.info("Alta de %s completada: %s", profile_url, stats)
    except Exception:
        logger.exception("Error en scrape de alta: %s", profile_url)


def _render_dashboard_html(stats: dict, searches: list) -> str:
    last = stats.get("last_run") or {}
    status_class = "ok" if last.get("status") == "success" else "err"

    rows = ""
    for s in searches:
        try:
            parsed = json.loads(s.get("parsed") or "{}")
        except Exception:
            parsed = {}
        loc   = parsed.get("location") or "—"
        rooms = parsed.get("rooms_min") or "—"
        rows += (
            f"<tr><td>#{_esc(s['id'])}</td>"
            f"<td>{_esc(s.get('contact_name') or '—')}</td>"
            f"<td>{_esc(loc)}</td>"
            f"<td>{_esc(rooms)}</td>"
            f"<td>{_esc(s.get('emails_sent', 0))}</td>"
            f"<td>{_esc(str(s.get('created_at', ''))[:16])}</td></tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jacobo-Bot — Dashboard</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:Arial,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#333}}
  h1{{color:#2c5282;border-bottom:2px solid #4299e1;padding-bottom:8px}}
  h2{{color:#2d3748;margin-top:32px}}
  .stats{{display:flex;gap:20px;flex-wrap:wrap;margin:20px 0}}
  .stat{{background:#f7fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 24px;min-width:160px}}
  .stat .n{{font-size:2.2em;font-weight:700;color:#2c5282}}
  .stat .label{{color:#718096;font-size:.9em}}
  table{{border-collapse:collapse;width:100%;margin-top:12px}}
  th{{background:#4299e1;color:#fff;padding:8px 12px;text-align:left;font-weight:600}}
  td{{padding:8px 12px;border-bottom:1px solid #e2e8f0}}
  tr:hover td{{background:#ebf8ff}}
  .badge{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.8em;font-weight:600}}
  .ok{{background:#c6f6d5;color:#276749}}
  .err{{background:#fed7d7;color:#c53030}}
  .na{{background:#e2e8f0;color:#4a5568}}
</style>
</head>
<body>
<h1>Jacobo-Bot — Dashboard</h1>
<div class="stats">
  <div class="stat"><div class="n">{stats.get('active_properties', 0)}</div><div class="label">Propiedades activas</div></div>
  <div class="stat"><div class="n">{stats.get('total_buyer_searches', 0)}</div><div class="label">Búsquedas recibidas</div></div>
</div>
<h2>Último ciclo de scraping</h2>
<table>
<tr><th>Inicio</th><th>Encontradas</th><th>Nuevas</th><th>Actualizadas</th><th>Eliminadas</th><th>Estado</th></tr>
<tr>
  <td>{_esc(str(last.get('started_at', '—'))[:16])}</td>
  <td>{last.get('properties_found', '—')}</td>
  <td>{last.get('properties_new', '—')}</td>
  <td>{last.get('properties_updated', '—')}</td>
  <td>{last.get('properties_removed', '—')}</td>
  <td><span class="badge {status_class if last else 'na'}">{_esc(last.get('status', '—'))}</span></td>
</tr>
</table>
<h2>Últimas búsquedas de compradores</h2>
<table>
<tr><th>#</th><th>Contacto</th><th>Zona</th><th>Habitaciones</th><th>Emails enviados</th><th>Fecha</th></tr>
{rows or '<tr><td colspan="6" style="text-align:center;color:#718096;padding:20px">Sin búsquedas todavía</td></tr>'}
</table>
<p style="color:#a0aec0;font-size:.8em;margin-top:32px">Actualizar la página para ver datos frescos.</p>
</body></html>"""


def _esc(s) -> str:
    return html.escape(str(s), quote=True)
