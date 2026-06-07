"""
Scraper para perfiles de agencias en Idealista.

Usa camoufox (Firefox anti-deteccion) como primer intento.
Fallback a Playwright Chromium con stealth si camoufox no esta instalado.

Cuando aparece captcha DataDome con --show-browser:
  -> El bot pausa automaticamente hasta que lo resuelves en el navegador
  -> No hace falta pulsar ENTER, detecta solo cuando desaparece el captcha

Si la IP esta bloqueada: conecta al hotspot del movil antes de ejecutar.
Para anadir perfiles: edita IDEALISTA_PROFILE_URLS en .env
"""

import json
import re
import shutil
import time
import random
import logging
from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

from config.settings import IDEALISTA_PROFILE_URLS, MAX_PROPERTIES_PER_AGENCY, SCRAPE_DO_TOKEN, SCRAPE_DO_TOKENS, SCRAPFLY_API_KEY, PROXY_SERVER, PROXY_USER, PROXY_PASSWORD

logger = logging.getLogger(__name__)

BASE_URL = "https://www.idealista.com"
SESSION_DIR = Path("data/browser_session")

_CAPTCHA_SIGNALS = [
    "desliza hacia la derecha",
    "muchas peticiones",
    "datadome",
    "uso indebido",
    "acceso se ha bloqueado",
    "asegurar tu acceso",
]

_COOKIE_SELECTORS = [
    "#didomi-notice-agree-button",
    "button[id*='accept']",
    "button:has-text('Aceptar todo')",
    "button:has-text('Aceptar')",
    "#onetrust-accept-btn-handler",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.112 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]

_WARMUP_PAGES = [
    "https://www.idealista.com/",
    "https://www.idealista.com/venta-viviendas/malaga-malaga/",
    "https://www.idealista.com/venta-viviendas/sevilla/",
    "https://www.idealista.com/alquiler-viviendas/madrid/",
    "https://www.idealista.com/venta-casas/malaga-malaga/",
    "https://www.idealista.com/venta-viviendas/marbella-malaga/",
    "https://www.idealista.com/venta-pisos/barcelona/",
    "https://www.idealista.com/venta-viviendas/granada/",
]


def _is_captcha(html: str) -> bool:
    low = html.lower()
    return any(s in low for s in _CAPTCHA_SIGNALS)


@dataclass
class Property:
    idealista_id: str
    url: str
    title: str
    price: Optional[int] = None
    price_text: str = ""
    location: str = ""
    area_m2: Optional[float] = None
    rooms: Optional[int] = None
    bathrooms: Optional[int] = None
    floor: str = ""
    description: str = ""
    photo_urls: list[str] = field(default_factory=list)
    is_floor_plan: list[bool] = field(default_factory=list)
    photo_labels: list[str] = field(default_factory=list)
    has_parking: bool = False
    has_pool: bool = False
    has_terrace: bool = False
    property_type: str = ""
    operation_type: str = "sale"
    raw_features: dict = field(default_factory=dict)


class IdealistaScraper:
    def __init__(self, delay_range: tuple[float, float] = (25.0, 55.0), headless: bool = False):
        self.delay_range = delay_range
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cookies_accepted = False
        self._use_camoufox = False
        self._captcha_count = 0
        self._current_ua = random.choice(_USER_AGENTS)
        self.seen_ids: set[str] = set()  # todos los IDs vistos en listings (incluye conocidas)
        self.failed_profiles: list[str] = []  # perfiles que fallaron al scrapear (no usar para detectar bajas)

    def _sleep(self, lo: float = None, hi: float = None):
        t = random.uniform(lo or self.delay_range[0], hi or self.delay_range[1])
        logger.debug("Pausa %.1fs", t)
        time.sleep(t)

    def _human_scroll(self):
        try:
            height = self._page.evaluate("document.body.scrollHeight")
            for i in range(random.randint(4, 7)):
                y = int(height * (i + 1) / 7 * random.uniform(0.6, 1.0))
                self._page.evaluate(f"window.scrollTo({{top:{y},behavior:'smooth'}})")
                time.sleep(random.uniform(0.6, 1.8))
            self._page.evaluate(f"window.scrollTo({{top:{random.randint(0,300)},behavior:'smooth'}})")
            time.sleep(random.uniform(0.4, 1.0))
        except Exception:
            pass

    def _accept_cookies(self):
        if self._cookies_accepted:
            return
        for sel in _COOKIE_SELECTORS:
            try:
                btn = self._page.wait_for_selector(sel, timeout=3000)
                if btn and btn.is_visible():
                    time.sleep(random.uniform(1.0, 2.5))
                    btn.click()
                    logger.info("Cookies aceptadas")
                    self._cookies_accepted = True
                    time.sleep(random.uniform(1.5, 2.5))
                    return
            except Exception:
                continue

    def _wait_captcha_solved(self, max_wait: int = 300):
        self._captcha_count += 1
        if not self.headless:
            logger.warning("")
            logger.warning("=" * 60)
            logger.warning("CAPTCHA detectado! (#%d en esta sesion)", self._captcha_count)
            logger.warning("-> Arrastra el slider en la ventana del navegador")
            logger.warning("-> El bot continuara solo cuando lo resuelvas")
            logger.warning("=" * 60)
            start = time.time()
            while time.time() - start < max_wait:
                time.sleep(3)
                try:
                    html = self._page.content()
                    if not _is_captcha(html):
                        extra = random.uniform(60, 120)
                        logger.info("Captcha resuelto! Esperando %.0fs adicionales para normalizar sesion...", extra)
                        time.sleep(extra)
                        return
                except Exception:
                    pass
            logger.warning("Timeout esperando captcha (%ds)", max_wait)
        else:
            logger.warning(
                "Captcha detectado (#%d). Esperando 120s... (usa --show-browser para resolverlo manualmente)",
                self._captcha_count,
            )
            time.sleep(120)

    # ------------------------------------------------------------------
    # Sesion y warm-up
    # ------------------------------------------------------------------

    def _clear_session(self):
        if SESSION_DIR.exists():
            shutil.rmtree(SESSION_DIR)
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Sesion del navegador limpiada (IP/cookies frescas).")

    def _warm_up(self):
        pages = random.sample(_WARMUP_PAGES, k=min(6, len(_WARMUP_PAGES)))
        logger.info("Warm-up: visitando %d paginas antes de scrapear...", len(pages))
        for i, url in enumerate(pages):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=45000)
                time.sleep(random.uniform(3.0, 7.0))
                self._accept_cookies()
                self._human_scroll()
                if i < len(pages) - 1:
                    self._sleep(12.0, 22.0)
            except Exception as e:
                logger.warning("Warm-up error en %s (no critico): %s", url, e)
        logger.info("Warm-up completado (%d paginas).", len(pages))

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _proxy_config(self) -> Optional[dict]:
        """Devuelve la config de proxy si está configurada en .env, o None."""
        if not PROXY_SERVER:
            return None
        cfg = {"server": PROXY_SERVER}
        if PROXY_USER:
            cfg["username"] = PROXY_USER
        if PROXY_PASSWORD:
            cfg["password"] = PROXY_PASSWORD
        logger.info("Usando proxy residencial: %s", PROXY_SERVER)
        return cfg

    def _start_browser(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        proxy = self._proxy_config()

        # Intentar camoufox primero (Firefox anti-deteccion)
        try:
            from camoufox.sync_api import Camoufox
            logger.info("Usando camoufox (Firefox anti-deteccion)")
            cam_kwargs = dict(headless=self.headless, humanize=True, locale="es-ES", geoip=True)
            if proxy:
                cam_kwargs["proxy"] = proxy
            self._camoufox_cm = Camoufox(**cam_kwargs)
            self._browser = self._camoufox_cm.__enter__()
            self._page = self._browser.new_page()
            self._use_camoufox = True
            return
        except Exception as e:
            logger.warning("camoufox no disponible (%s), usando Playwright Chromium", e)

        # Fallback: Playwright Chromium con stealth
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
            self._stealth_fn = stealth_sync
        except ImportError:
            self._stealth_fn = None

        logger.info("User-Agent seleccionado: %s", self._current_ua[:60])
        self._pw = sync_playwright().start()
        launch_kwargs = dict(
            user_data_dir=str(SESSION_DIR),
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            user_agent=self._current_ua,
            locale="es-ES",
            viewport={"width": 1366, "height": 768},
        )
        if proxy:
            launch_kwargs["proxy"] = proxy
        self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        self._context.add_init_script("""
            Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
            Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});
            Object.defineProperty(navigator,'languages',{get:()=>['es-ES','es']});
            window.chrome={runtime:{}};
        """)
        self._page = self._context.new_page()
        if self._stealth_fn:
            self._stealth_fn(self._page)

    def _stop_browser(self):
        try:
            if self._use_camoufox and hasattr(self, '_camoufox_cm'):
                self._camoufox_cm.__exit__(None, None, None)
            elif self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Carga de pagina
    # ------------------------------------------------------------------

    def _get_html_via_scrapfly(self, url: str, retries: int = 2) -> Optional[str]:
        """Scrapfly con ASP (pasa DataDome). Cuesta ~25 créditos por request."""
        is_detail = "/inmueble/" in url
        # JS solo necesario para listings dinámicos; detalles sirven con HTML estático
        params = {
            "key": SCRAPFLY_API_KEY,
            "url": url,
            "asp": "true",
            "country": "es",
        }
        if is_detail:
            params["render_js"] = "false"
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        api_url = f"https://api.scrapfly.io/scrape?{qs}"
        for attempt in range(retries):
            try:
                resp = requests.get(api_url, timeout=120)
                if resp.status_code == 422:
                    logger.error("Scrapfly: configuración inválida — body: %s", resp.text[:300])
                    return None
                if resp.status_code == 429:
                    logger.warning("Scrapfly rate limit — esperando 30s")
                    time.sleep(30)
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {})
                    if result.get("success") and result.get("status_code") == 200:
                        html = result.get("content", "")
                        cost = data.get("context", {}).get("cost", {}).get("total", "?")
                        if html and len(html) > 5000 and not _is_captcha(html):
                            logger.info("Scrapfly OK [%s créditos]: %s (%d chars)", cost, url, len(html))
                            return html
                        logger.warning("Scrapfly: HTML inválido/captcha — len=%d", len(html))
                    else:
                        logger.warning("Scrapfly: result.success=False — status=%s", result.get("status_code"))
                else:
                    logger.warning("Scrapfly intento %d: HTTP %d body=%s", attempt + 1, resp.status_code, resp.text[:200])
            except Exception as e:
                logger.error("Scrapfly intento %d: %s", attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
        logger.error("Scrapfly: agotados todos los intentos para %s", url)
        return None

    def _get_html_via_scrapedo(self, url: str, retries: int = 2) -> Optional[str]:
        is_detail = "/inmueble/" in url
        extra = "&timeout=15000" if is_detail else ""
        tokens = SCRAPE_DO_TOKENS if SCRAPE_DO_TOKENS else [SCRAPE_DO_TOKEN]
        variants = [
            {"super": "true", "geoCode": "ES"},
            {"render": "true", "geoCode": "ES"},
            {"render": "true"},
        ]
        dead_tokens: set[int] = set()  # índices de tokens definitivamente inválidos
        for params in variants:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            for token_idx, token in enumerate(tokens):
                if token_idx in dead_tokens:
                    continue
                for attempt in range(retries):
                    try:
                        api_url = f"https://api.scrape.do/?token={token}&url={quote(url, safe='')}&{qs}{extra}"
                        resp = requests.get(api_url, timeout=60)
                        if resp.status_code == 404:
                            logger.warning("Scrape.do: 404 en %s — perfil inexistente, saltando", url)
                            return None
                        if resp.status_code == 401:
                            body = resp.text[:400]
                            if "inactive or incorrect" in body:
                                logger.error("Scrape.do token %d inválido/inactivo — marcando como muerto. Verifica el dashboard.", token_idx + 1)
                                dead_tokens.add(token_idx)
                                break
                            if "throttled" in body:
                                logger.warning("Scrape.do throttle — esperando 35s antes de continuar")
                                time.sleep(35)
                                break
                            # límite mensual agotado
                            logger.warning("Scrape.do token %d agotado (límite mensual) — rotando", token_idx + 1)
                            break
                        if resp.status_code == 200 and len(resp.text) > 5000 and not _is_captcha(resp.text):
                            logger.info("Scrape.do OK [token%d/%s]: %s (%d chars)", token_idx + 1, qs, url, len(resp.text))
                            return resp.text
                        logger.warning("Scrape.do [token%d/%s] intento %d: status=%d body=%s", token_idx + 1, qs, attempt + 1, resp.status_code, resp.text[:200])
                    except Exception as e:
                        logger.error("Scrape.do [token%d/%s] error (intento %d): %s", token_idx + 1, qs, attempt + 1, e)
                    if attempt < retries - 1:
                        time.sleep(5 * (attempt + 1))
        logger.error("Scrape.do: todos los tokens agotados para %s", url)
        return None

    def _get_html(self, url: str, retries: int = 3) -> Optional[str]:
        # Prioridad: Scrapfly (mejor anti-DataDome) > Scrape.do > navegador
        if SCRAPFLY_API_KEY:
            return self._get_html_via_scrapfly(url, retries)
        if SCRAPE_DO_TOKEN:
            return self._get_html_via_scrapedo(url, retries)

        for attempt in range(retries):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=45000)
                time.sleep(random.uniform(2.5, 5.0))
                self._accept_cookies()
                self._human_scroll()
                html = self._page.content()

                if _is_captcha(html):
                    self._wait_captcha_solved()
                    self._page.goto(url, wait_until="networkidle", timeout=45000)
                    time.sleep(3)
                    self._accept_cookies()
                    html = self._page.content()

                if html and len(html) > 5000 and not _is_captcha(html):
                    return html

                if attempt < retries - 1:
                    logger.warning("Pagina incompleta, reintentando en 15s...")
                    time.sleep(15)
            except Exception as e:
                logger.error("Error cargando %s (intento %d): %s", url, attempt + 1, e)
                if attempt < retries - 1:
                    time.sleep(10 * (attempt + 1))
        return None

    def _get_soup(self, url: str) -> Optional[BeautifulSoup]:
        html = self._get_html(url)
        return BeautifulSoup(html, "lxml") if html else None

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def scrape_profile(self, profile_url: str, known_ids: set[str] = None) -> list[Property]:
        limit = MAX_PROPERTIES_PER_AGENCY
        known_ids = known_ids or set()
        logger.info("Scrapeando perfil: %s (límite: %s)", profile_url, limit or "sin límite")
        properties: list[Property] = []
        fetch_failed = False
        page = 1
        while True:
            url = self._profile_page_url(profile_url, page)
            soup = self._get_soup(url)
            if soup is None:
                # _get_soup solo devuelve None ante un fallo de fetch (rate-limit, ban,
                # error de red), NUNCA por "no hay más propiedades" (eso da listing vacío).
                # Marcamos el perfil como fallido para que el monitor NO interprete sus
                # propiedades como bajas y las pause/borre por error.
                logger.warning("Fetch falló en %s (página %d) — perfil marcado como fallido", profile_url, page)
                fetch_failed = True
                break
            item_urls = self._parse_listing_page(soup)
            if not item_urls:
                if page == 1:
                    # Página 1 sin NINGUNA propiedad = soft-block o cambio de markup,
                    # NO una agencia vacía. Un perfil real SIEMPRE lista inmuebles.
                    # El fetch puede devolver 200 + HTML >5000 chars que no es un captcha
                    # reconocido (bloqueo blando de Scrapfly/DataDome), así que _get_soup
                    # no devuelve None y el blindaje de failed_profiles no se activaría.
                    # Lo marcamos como fallo para que el monitor NO pause/borre estas
                    # propiedades por error (mismo blindaje que un fetch fallido).
                    logger.warning(
                        "Perfil %s devolvió 0 propiedades en página 1 — probable soft-block/cambio de markup. "
                        "Marcado como fallido (no se usará para detectar bajas).",
                        profile_url,
                    )
                    fetch_failed = True
                else:
                    logger.info("Sin mas propiedades en pagina %d. Total: %d", page, len(properties))
                break
            if limit:
                remaining = limit - len(properties)
                item_urls = item_urls[:remaining]
            for item_url in item_urls:
                prop_id = self._extract_id(item_url)
                if prop_id:
                    self.seen_ids.add(prop_id)
                if prop_id and prop_id in known_ids:
                    logger.debug("Propiedad %s ya en BD — saltando detalle", prop_id)
                    continue
                prop = self._scrape_property(item_url)
                if prop:
                    properties.append(prop)
                self._sleep()
            if (limit and len(properties) >= limit) or not self._has_next_page(soup):
                break
            page += 1
            self._sleep()

        # Eliminar URLs que aparecen en 2+ propiedades (logos/avatares de agencia)
        if len(properties) > 1:
            url_counts = Counter(u for p in properties for u in p.photo_urls)
            shared = {u for u, n in url_counts.items() if n > 1}
            if shared:
                logger.debug("Filtrando %d URLs compartidas (logos/avatares)", len(shared))
                for p in properties:
                    kept = [(u, f, l) for u, f, l in zip(p.photo_urls, p.is_floor_plan, p.photo_labels) if u not in shared]
                    if kept:
                        p.photo_urls, p.is_floor_plan, p.photo_labels = map(list, zip(*kept))
                    else:
                        p.photo_urls, p.is_floor_plan, p.photo_labels = [], [], []

        if fetch_failed:
            self.failed_profiles.append(profile_url)

        return properties

    def scrape_all_profiles(self, known_ids: set[str] = None) -> list[Property]:
        if not IDEALISTA_PROFILE_URLS:
            logger.warning("IDEALISTA_PROFILE_URLS vacio en .env")
            return []

        if SCRAPFLY_API_KEY or SCRAPE_DO_TOKEN:
            backend = "Scrapfly" if SCRAPFLY_API_KEY else "Scrape.do"
            logger.info("Modo %s activo — sin navegador, sin captchas", backend)
            self.delay_range = (2.0, 5.0)
            self.seen_ids.clear()
            self.failed_profiles.clear()
            all_props: list[Property] = []
            for url in IDEALISTA_PROFILE_URLS:
                logger.info("--- Perfil: %s ---", url)
                all_props.extend(self.scrape_profile(url, known_ids=known_ids))
                self._sleep(2.0, 4.0)
            return all_props

        self.seen_ids.clear()
        self.failed_profiles.clear()
        self._clear_session()
        self._start_browser()
        try:
            self._warm_up()
            all_props = []
            for url in IDEALISTA_PROFILE_URLS:
                logger.info("--- Perfil: %s ---", url)
                all_props.extend(self.scrape_profile(url, known_ids=known_ids))
                self._sleep(15.0, 30.0)
            return all_props
        finally:
            self._stop_browser()

    @staticmethod
    def _profile_page_url(profile_url: str, page: int) -> str:
        url = profile_url.rstrip("/")
        return url if page == 1 else f"{url}/pagina-{page}.htm"

    @staticmethod
    def _has_next_page(soup: BeautifulSoup) -> bool:
        return bool(soup.find("link", {"rel": "next"}) or soup.select_one("a.icon-arrow-right-after"))

    @staticmethod
    def _clean_url(url: str) -> str:
        """Elimina query params (Google Translate, tracking, etc.) de URLs de Idealista."""
        return url.split("?")[0]

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[str]:
        try:
            hrefs: list[str] = self._page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href).filter(h => h.includes('/inmueble/'))
            """)
            seen: set[str] = set()
            urls = []
            for h in hrefs:
                h = self._clean_url(h)
                if h not in seen:
                    seen.add(h)
                    urls.append(h)
            if urls:
                logger.info("Encontradas %d propiedades", len(urls))
                return urls
        except Exception as e:
            logger.warning("JS eval fallo: %s", e)
        seen = set()
        urls = []
        for a in soup.find_all("a", href=re.compile(r"/inmueble/\d+")):
            href = a["href"]
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            href = self._clean_url(href)
            if href not in seen:
                seen.add(href)
                urls.append(href)
        return urls

    def _scrape_property(self, url: str) -> Optional[Property]:
        logger.info("  -> Propiedad: %s", url)
        soup = self._get_soup(url)
        if soup is None:
            return None
        prop_id = self._extract_id(url)
        if not prop_id:
            return None
        prop = Property(
            idealista_id=prop_id, url=url,
            title=self._text_first(soup, ["h1.main-info__title", "h1.jumbotron-title", "h1"]),
            price_text=self._text_first(soup, ["span.info-data-price", "span.price-row"]),
            location=self._text_first(soup, ["span.main-info__title-minor", "li.header-map-list span"]),
            description=self._extract_description(soup),
            property_type=self._detect_property_type(soup),
            operation_type=self._detect_operation_type(url),
        )
        prop.price = self._parse_price(prop.price_text)
        self._parse_features(soup, prop)
        prop.photo_urls, prop.is_floor_plan, prop.photo_labels = self._extract_photos(soup)
        logger.debug("Fotos extraidas para %s: %d URLs", prop.idealista_id, len(prop.photo_urls))
        non_empty_labels = [l for l in prop.photo_labels if l]
        if non_empty_labels:
            logger.debug("Labels de fotos para %s: %s", prop.idealista_id, non_empty_labels)
        if not prop.photo_urls:
            logger.warning("Sin fotos para %s — buscando URLs de imagen en HTML...", prop.idealista_id)
            html_str = str(soup)
            img_matches = re.findall(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', html_str, re.IGNORECASE)
            unique_imgs = list(dict.fromkeys(img_matches))[:5]
            if unique_imgs:
                logger.warning("  URLs de imagen encontradas en HTML: %s", unique_imgs)
            else:
                logger.warning("  No se encontraron URLs de imagen en el HTML")
            # Buscar claves JS relacionadas con multimedia
            for script in soup.find_all("script", string=True):
                text = script.string or ""
                for key in ('"images"', '"photos"', '"multimedia"', '"gallery"', '"pictures"', 'idealista.com/'):
                    if key in text:
                        snippet = text[max(0, text.index(key)-20):text.index(key)+200]
                        logger.warning("  Clave '%s' en script: ...%s...", key, snippet)
                        break
        return prop

    @staticmethod
    def _deduplicate_photos(urls: list[str], flags: list[bool], labels: list[str]) -> tuple[list[str], list[bool], list[str]]:
        """Elimina duplicados: si la misma imagen existe en .webp y .jpg, conserva solo una."""
        seen_base: dict[str, tuple[str, bool, str]] = {}
        order: list[str] = []
        for url, flag, label in zip(urls, flags, labels):
            base = re.sub(r'\.[a-z]{2,4}$', '', url.split("/")[-1].split("?")[0])
            if base not in seen_base:
                seen_base[base] = (url, flag, label)
                order.append(base)
            else:
                # Preferir webp sobre jpg sobre otros
                existing_url = seen_base[base][0]
                if url.endswith(".webp") and not existing_url.endswith(".webp"):
                    seen_base[base] = (url, flag, label)
        return [seen_base[b][0] for b in order], [seen_base[b][1] for b in order], [seen_base[b][2] for b in order]

    def _extract_photos(self, soup: BeautifulSoup) -> tuple[list[str], list[bool], list[str]]:
        urls, flags, labels = self._photos_from_embedded_json(soup)
        if urls:
            return self._deduplicate_photos(urls, flags, labels)
        urls, flags, labels = self._photos_from_html_gallery(soup)
        if urls:
            return self._deduplicate_photos(urls, flags, labels)
        urls, flags, labels = self._photos_from_jsonld(soup)
        return self._deduplicate_photos(urls, flags, labels)

    @staticmethod
    def _photos_from_embedded_json(soup: BeautifulSoup) -> tuple[list[str], list[bool], list[str]]:
        urls, is_plan, labels = [], [], []
        for script in soup.find_all("script", string=True):
            text = script.string or ""
            m = re.search(r'"images"\s*:\s*(\[.*?\])', text, re.DOTALL)
            if m:
                try:
                    for img in json.loads(m.group(1)):
                        if isinstance(img, dict):
                            u = _normalize_image_url(img.get("url", ""))
                            if u:
                                urls.append(u)
                                tag = (img.get("tag") or "").strip()
                                is_plan.append("plano" in tag.lower())
                                labels.append(tag)
                    if urls:
                        return urls, is_plan, labels
                except Exception:
                    pass
        return [], [], []

    @staticmethod
    def _photos_from_html_gallery(soup: BeautifulSoup) -> tuple[list[str], list[bool], list[str]]:
        _STATIC_SKIP = ("static/common", "icons/", "favicon", ".svg", ".gif", "avatar", "profilephotos")
        urls: list[str] = []
        is_plan: list[bool] = []
        labels: list[str] = []
        seen: set[str] = set()

        def _add(src: str, alt: str = "") -> None:
            src = _normalize_image_url(src)
            if src and src.startswith("http") and src not in seen and not any(s in src for s in _STATIC_SKIP):
                seen.add(src)
                urls.append(src)
                is_plan.append("plano" in alt.lower())
                labels.append(alt.strip())

        # 1. Selectores CSS específicos de galería Idealista (recorre TODOS, sin retorno anticipado)
        gallery_selectors = [
            "picture.gallery-image source",
            "img.gallery-image",
            "div.gallery img",
            ".main-slider img",
            ".detail-images img",
            "li.image img",
        ]
        for sel in gallery_selectors:
            for el in soup.select(sel):
                if el.name == "source":
                    parts = [p.strip() for p in el.get("srcset", "").split(",") if p.strip()]
                    src = parts[-1].split(" ")[0] if parts else ""
                else:
                    src = (el.get("data-ondemand-img") or el.get("data-src")
                           or el.get("data-original") or el.get("src") or "")
                _add(src, el.get("alt", ""))

        # 2. Regex sobre el HTML completo — captura todas las fotos WEB_DETAIL de Idealista
        for raw_url in re.findall(
            r'https://img\d+\.idealista\.com/blur/WEB_DETAIL/[^\s"\'<>]+', str(soup)
        ):
            _add(raw_url)

        if urls:
            return urls, is_plan, labels

        # 3. Fallback amplio — deduplicado por nombre de archivo base
        for el in soup.find_all(["img", "source"]):
            src = ""
            for attr in ("data-ondemand-img", "data-src", "data-original", "src", "srcset"):
                raw = el.get(attr, "")
                if not raw:
                    continue
                if attr == "srcset":
                    parts = [p.strip().split(" ")[0] for p in raw.split(",") if p.strip()]
                    raw = parts[-1] if parts else ""
                candidate = _normalize_image_url(raw)
                if (candidate and candidate.startswith("http")
                        and any(ext in candidate for ext in (".jpg", ".jpeg", ".png", ".webp"))
                        and not any(s in candidate for s in _STATIC_SKIP)):
                    src = candidate
                    break
            if src:
                base = re.sub(r'\.[a-z]{2,4}$', '', src.split("/")[-1])
                if base and base not in seen:
                    seen.add(base)
                    urls.append(src)
                    alt = (el.get("alt") or "").strip()
                    is_plan.append("plano" in alt.lower())
                    labels.append(alt)
            if len(urls) >= 25:
                break

        return urls, is_plan, labels

    @staticmethod
    def _photos_from_jsonld(soup: BeautifulSoup) -> tuple[list[str], list[bool], list[str]]:
        urls = []
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                imgs = data.get("image", [])
                if isinstance(imgs, str):
                    imgs = [imgs]
                for img in imgs:
                    if isinstance(img, dict):
                        img = img.get("url", "")
                    if isinstance(img, str) and img:
                        urls.append(_normalize_image_url(img))
            except Exception:
                continue
        return urls, [False] * len(urls), [""] * len(urls)

    @staticmethod
    def _extract_id(url: str) -> Optional[str]:
        m = re.search(r"/inmueble/(\d+)/", url)
        return m.group(1) if m else None

    @staticmethod
    def _text_first(soup: BeautifulSoup, selectors: list[str]) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        return ""

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        for sel in ["div.comment", "div[class*='description']", "section.comment"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator=" ", strip=True)
        return ""

    @staticmethod
    def _parse_price(text: str) -> Optional[int]:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None

    def _parse_features(self, soup: BeautifulSoup, prop: Property):
        items: list[str] = []
        for sel in ["div.details-property_features ul li", "ul.details-property_features li", "div[class*='feature'] li"]:
            els = soup.select(sel)
            if els:
                items = [el.get_text(strip=True) for el in els]
                break
        for text in items:
            lower = text.lower()
            if not prop.area_m2:
                m = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", lower)
                if m:
                    prop.area_m2 = float(m.group(1).replace(",", "."))
            if not prop.rooms:
                m = re.search(r"(\d+)\s*(?:dormitorio|habitaci[oó]n)", lower)
                if m:
                    prop.rooms = int(m.group(1))
            if not prop.bathrooms:
                m = re.search(r"(\d+)\s*ba[ñn]o", lower)
                if m:
                    prop.bathrooms = int(m.group(1))
            if any(w in lower for w in ("parking", "garaje", "plaza de")):
                prop.has_parking = True
            if "piscina" in lower:
                prop.has_pool = True
            if any(w in lower for w in ("terraza", "balc")):
                prop.has_terrace = True
            if not prop.floor:
                m = re.search(r"planta\s+(\w+)", lower)
                if m:
                    prop.floor = m.group(1)

    @staticmethod
    def _detect_property_type(soup: BeautifulSoup) -> str:
        texts = [li.get_text(strip=True).lower() for li in soup.select("ol.breadcrumb li")]
        types = ("piso", "casa", "chalet", "apartamento", "estudio", "ático", "local", "garaje", "terreno", "villa")
        for t in texts:
            for pt in types:
                if pt in t:
                    return pt
        title = (soup.select_one("h1") or BeautifulSoup("", "lxml")).get_text().lower()
        return next((pt for pt in types if pt in title), "propiedad")

    @staticmethod
    def _detect_operation_type(url: str) -> str:
        return "rent" if "/alquiler/" in url else "sale"


def _normalize_image_url(url: str) -> str:
    if not url:
        return ""
    url = re.sub(r"/(?:WEB_DETAIL|WEB_LISTING|DETAIL|LISTING)-[A-Z0-9\-]+/", "/", url.strip())
    return url.split("?")[0]
