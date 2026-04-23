"""
Scraper para perfiles de agencias en Idealista.

Estrategia anti-DataDome:
  1. Sesion persistente (guarda cookies entre ejecuciones en data/browser_session/)
  2. Warm-up: visita la home y navega un poco antes de ir al perfil
  3. Delays largos y scroll humano
  4. Si aparece captcha con --show-browser: pausa para resolverlo manualmente

IMPORTANTE: Si la IP esta bloqueada, cambia de red (hotspot movil) antes de ejecutar.
Para anadir perfiles: edita IDEALISTA_PROFILE_URLS en .env
"""

import json
import re
import time
import random
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config.settings import IDEALISTA_PROFILE_URLS

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
    "button#aceptar",
    "button[id*='accept']",
    "button:has-text('Aceptar todo')",
    "button:has-text('Aceptar')",
    "#onetrust-accept-btn-handler",
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
    has_parking: bool = False
    has_pool: bool = False
    has_terrace: bool = False
    property_type: str = ""
    operation_type: str = "sale"
    raw_features: dict = field(default_factory=dict)


class IdealistaScraper:
    def __init__(self, delay_range: tuple[float, float] = (8.0, 20.0), headless: bool = True):
        self.delay_range = delay_range
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cookies_accepted = False

    # ------------------------------------------------------------------
    # Delays y comportamiento humano
    # ------------------------------------------------------------------

    def _sleep(self, low: float = None, high: float = None):
        lo = low or self.delay_range[0]
        hi = high or self.delay_range[1]
        t = random.uniform(lo, hi)
        logger.debug("Pausa de %.1fs", t)
        time.sleep(t)

    def _human_scroll(self):
        try:
            height = self._page.evaluate("document.body.scrollHeight")
            steps = random.randint(4, 8)
            for i in range(steps):
                y = int((height / steps) * (i + 1) * random.uniform(0.6, 1.0))
                self._page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'smooth'}})")
                time.sleep(random.uniform(0.5, 1.8))
            # Volver arriba parcialmente
            self._page.evaluate(f"window.scrollTo({{top: {random.randint(0, 300)}, behavior: 'smooth'}})")
            time.sleep(random.uniform(0.5, 1.2))
        except Exception:
            pass

    def _accept_cookies(self):
        if self._cookies_accepted:
            return
        try:
            for sel in _COOKIE_SELECTORS:
                try:
                    btn = self._page.wait_for_selector(sel, timeout=3000)
                    if btn and btn.is_visible():
                        time.sleep(random.uniform(1.5, 3.0))
                        btn.click()
                        logger.info("Cookies aceptadas")
                        self._cookies_accepted = True
                        time.sleep(random.uniform(1.5, 2.5))
                        return
                except Exception:
                    continue
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Warm-up: navegar la home antes de ir al perfil
    # ------------------------------------------------------------------

    def _warm_up(self):
        """
        Visita la homepage de Idealista y navega brevemente para
        establecer cookies y parecer un usuario real antes de ir al perfil.
        """
        logger.info("Iniciando warm-up en idealista.com...")
        try:
            self._page.goto("https://www.idealista.com/", wait_until="networkidle", timeout=45000)
            time.sleep(random.uniform(3.0, 5.0))
            self._accept_cookies()
            self._human_scroll()
            self._sleep(5.0, 12.0)

            # Visitar una busqueda generica para simular interes real
            self._page.goto(
                "https://www.idealista.com/venta-viviendas/malaga-malaga/",
                wait_until="networkidle",
                timeout=45000,
            )
            time.sleep(random.uniform(3.0, 5.0))
            self._accept_cookies()
            self._human_scroll()
            self._sleep(6.0, 14.0)
            logger.info("Warm-up completado. Procediendo al scraping.")
        except Exception as e:
            logger.warning("Warm-up fallo (no critico): %s", e)

    # ------------------------------------------------------------------
    # Browser con sesion persistente
    # ------------------------------------------------------------------

    def _start_browser(self):
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
            self._stealth_fn = stealth_sync
        except ImportError:
            logger.warning("playwright-stealth no instalado: pip install playwright-stealth")
            self._stealth_fn = None

        SESSION_DIR.mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()

        # Contexto persistente: guarda cookies/localStorage entre ejecuciones
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
        )
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es'] });
            window.chrome = { runtime: {} };
        """)
        self._page = self._context.new_page()
        if self._stealth_fn:
            self._stealth_fn(self._page)

    def _stop_browser(self):
        try:
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Captcha handler
    # ------------------------------------------------------------------

    def _handle_captcha(self, url: str):
        if not self.headless:
            logger.warning("\n" + "=" * 60)
            logger.warning("CAPTCHA en: %s", url)
            logger.warning("Resuelve el slider en la ventana del navegador")
            logger.warning("=" * 60)
            input("  >> ENTER cuando lo hayas resuelto: ")
            self._page.wait_for_timeout(3000)
        else:
            logger.warning("Captcha detectado. Esperando 120s... (usa --show-browser para resolverlo)")
            time.sleep(120)

    # ------------------------------------------------------------------
    # Carga de pagina
    # ------------------------------------------------------------------

    def _get_html(self, url: str, retries: int = 3) -> Optional[str]:
        for attempt in range(retries):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=45000)
                time.sleep(random.uniform(2.5, 5.0))
                self._accept_cookies()
                self._human_scroll()

                html = self._page.content()

                if _is_captcha(html):
                    self._handle_captcha(url)
                    self._page.goto(url, wait_until="networkidle", timeout=45000)
                    time.sleep(4)
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

    def scrape_profile(self, profile_url: str) -> list[Property]:
        logger.info("Scrapeando perfil: %s", profile_url)
        properties: list[Property] = []
        page = 1
        while True:
            url = self._profile_page_url(profile_url, page)
            soup = self._get_soup(url)
            if soup is None:
                break
            item_urls = self._parse_listing_page(soup)
            if not item_urls:
                logger.info("Sin mas propiedades en pagina %d. Total: %d", page, len(properties))
                break
            for item_url in item_urls:
                prop = self._scrape_property(item_url)
                if prop:
                    properties.append(prop)
                self._sleep()
            if not self._has_next_page(soup):
                break
            page += 1
            self._sleep()
        return properties

    def scrape_all_profiles(self) -> list[Property]:
        if not IDEALISTA_PROFILE_URLS:
            logger.warning("IDEALISTA_PROFILE_URLS vacio en .env")
            return []
        self._start_browser()
        try:
            self._warm_up()
            all_props: list[Property] = []
            for url in IDEALISTA_PROFILE_URLS:
                logger.info("--- Perfil: %s ---", url)
                all_props.extend(self.scrape_profile(url))
                self._sleep(15.0, 30.0)  # Pausa larga entre perfiles
            return all_props
        finally:
            self._stop_browser()

    # ------------------------------------------------------------------
    # Paginacion
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_page_url(profile_url: str, page: int) -> str:
        url = profile_url.rstrip("/")
        return url if page == 1 else f"{url}/pagina-{page}.htm"

    @staticmethod
    def _has_next_page(soup: BeautifulSoup) -> bool:
        if soup.find("link", {"rel": "next"}):
            return True
        return bool(soup.select_one("a.icon-arrow-right-after"))

    # ------------------------------------------------------------------
    # Extraccion de URLs de propiedades
    # ------------------------------------------------------------------

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[str]:
        try:
            hrefs: list[str] = self._page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h.includes('/inmueble/'))
            """)
            seen: set[str] = set()
            urls = [h for h in hrefs if not (h in seen or seen.add(h))]
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
            if href not in seen:
                seen.add(href)
                urls.append(href)
        return urls

    # ------------------------------------------------------------------
    # Scraping de propiedad individual
    # ------------------------------------------------------------------

    def _scrape_property(self, url: str) -> Optional[Property]:
        logger.info("  -> Propiedad: %s", url)
        soup = self._get_soup(url)
        if soup is None:
            return None
        prop_id = self._extract_id(url)
        if not prop_id:
            return None
        prop = Property(
            idealista_id=prop_id,
            url=url,
            title=self._text_first(soup, ["h1.main-info__title", "h1.jumbotron-title", "h1"]),
            price_text=self._text_first(soup, ["span.info-data-price", "span.price-row"]),
            location=self._text_first(soup, [
                "span.main-info__title-minor",
                "li.header-map-list span",
                "div.main-info__title-minor",
            ]),
            description=self._extract_description(soup),
            property_type=self._detect_property_type(soup),
            operation_type=self._detect_operation_type(url),
        )
        prop.price = self._parse_price(prop.price_text)
        self._parse_features(soup, prop)
        prop.photo_urls, prop.is_floor_plan = self._extract_photos(soup)
        return prop

    # ------------------------------------------------------------------
    # Extraccion de fotos
    # ------------------------------------------------------------------

    def _extract_photos(self, soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls, flags = self._photos_from_embedded_json(soup)
        if urls:
            return urls, flags
        urls, flags = self._photos_from_html_gallery(soup)
        if urls:
            return urls, flags
        return self._photos_from_jsonld(soup)

    @staticmethod
    def _photos_from_embedded_json(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls: list[str] = []
        is_plan: list[bool] = []
        for script in soup.find_all("script", string=True):
            text = script.string or ""
            m = re.search(r'"images"\s*:\s*(\[.*?\])', text, re.DOTALL)
            if m:
                try:
                    for img in json.loads(m.group(1)):
                        if not isinstance(img, dict):
                            continue
                        img_url = _normalize_image_url(img.get("url", ""))
                        tag = (img.get("tag") or "").lower()
                        if img_url:
                            urls.append(img_url)
                            is_plan.append("plano" in tag or "floor" in tag)
                    if urls:
                        return urls, is_plan
                except (json.JSONDecodeError, TypeError):
                    pass
        return [], []

    @staticmethod
    def _photos_from_html_gallery(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls: list[str] = []
        is_plan: list[bool] = []
        for selector in ["picture.gallery-image source", "img.gallery-image", "div.gallery img"]:
            for el in soup.select(selector):
                if el.name == "source":
                    srcset = el.get("srcset", "")
                    parts = [p.strip() for p in srcset.split(",") if p.strip()]
                    src = parts[-1].split(" ")[0] if parts else ""
                else:
                    src = el.get("src") or el.get("data-src") or ""
                src = _normalize_image_url(src)
                if src and src.startswith("http"):
                    urls.append(src)
                    is_plan.append("plano" in (el.get("alt") or "").lower())
            if urls:
                break
        return urls, is_plan

    @staticmethod
    def _photos_from_jsonld(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls: list[str] = []
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                for img in ([data.get("image")] if isinstance(data.get("image"), str) else data.get("image", [])):
                    if isinstance(img, dict):
                        img = img.get("url", "")
                    if isinstance(img, str) and img:
                        urls.append(_normalize_image_url(img))
            except Exception:
                continue
        return urls, [False] * len(urls)

    # ------------------------------------------------------------------
    # Parsers auxiliares
    # ------------------------------------------------------------------

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
        for sel in [
            "div.details-property_features ul li",
            "ul.details-property_features li",
            "div[class*='feature'] li",
        ]:
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
        texts = [li.get_text(strip=True).lower()
                 for li in soup.select("ol.breadcrumb li, nav[aria-label*='breadcrumb'] li")]
        types = ("piso", "casa", "chalet", "apartamento", "estudio",
                 "ático", "local", "oficina", "garaje", "terreno", "villa")
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
