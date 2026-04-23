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
        self._use_camoufox = False

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

    def _wait_captcha_solved(self, url: str, max_wait: int = 300):
        """
        Cuando aparece captcha con navegador visible:
        muestra mensaje y espera automaticamente hasta que el usuario
        lo resuelva (detecta cuando desaparece el captcha).
        """
        if not self.headless:
            logger.warning("")
            logger.warning("=" * 60)
            logger.warning("CAPTCHA detectado!")
            logger.warning("-> Arrastra el slider en la ventana del navegador")
            logger.warning("-> El bot continuara solo cuando lo resuelvas")
            logger.warning("=" * 60)
            start = time.time()
            while time.time() - start < max_wait:
                time.sleep(3)
                try:
                    html = self._page.content()
                    if not _is_captcha(html):
                        logger.info("Captcha resuelto! Continuando...")
                        time.sleep(2)
                        return
                except Exception:
                    pass
            logger.warning("Timeout esperando captcha (%ds)", max_wait)
        else:
            logger.warning("Captcha detectado. Esperando 120s... (usa --show-browser para resolverlo tu mismo)")
            time.sleep(120)

    # ------------------------------------------------------------------
    # Warm-up humano
    # ------------------------------------------------------------------

    def _warm_up(self):
        logger.info("Warm-up: navegando como humano antes de ir al perfil...")
        try:
            self._page.goto("https://www.idealista.com/", wait_until="networkidle", timeout=45000)
            time.sleep(random.uniform(3.0, 5.0))
            self._accept_cookies()
            self._human_scroll()
            self._sleep(6.0, 12.0)

            self._page.goto(
                "https://www.idealista.com/venta-viviendas/malaga-malaga/",
                wait_until="networkidle", timeout=45000,
            )
            time.sleep(random.uniform(3.0, 6.0))
            self._accept_cookies()
            self._human_scroll()
            self._sleep(8.0, 15.0)
            logger.info("Warm-up completado.")
        except Exception as e:
            logger.warning("Warm-up error (no critico): %s", e)

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def _start_browser(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)

        # Intentar camoufox primero (Firefox anti-deteccion)
        try:
            from camoufox.sync_api import Camoufox
            logger.info("Usando camoufox (Firefox anti-deteccion)")
            self._camoufox_cm = Camoufox(
                headless=self.headless,
                humanize=True,
                locale="es-ES",
                geoip=True,
            )
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

        self._pw = sync_playwright().start()
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
        )
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

    def _get_html(self, url: str, retries: int = 3) -> Optional[str]:
        for attempt in range(retries):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=45000)
                time.sleep(random.uniform(2.5, 5.0))
                self._accept_cookies()
                self._human_scroll()
                html = self._page.content()

                if _is_captcha(html):
                    self._wait_captcha_solved(url)
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

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[str]:
        try:
            hrefs: list[str] = self._page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href).filter(h => h.includes('/inmueble/'))
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
        prop.photo_urls, prop.is_floor_plan = self._extract_photos(soup)
        return prop

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
        urls, is_plan = [], []
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
                                is_plan.append("plano" in (img.get("tag") or "").lower())
                    if urls:
                        return urls, is_plan
                except Exception:
                    pass
        return [], []

    @staticmethod
    def _photos_from_html_gallery(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls, is_plan = [], []
        for sel in ["picture.gallery-image source", "img.gallery-image", "div.gallery img"]:
            for el in soup.select(sel):
                src = ""
                if el.name == "source":
                    parts = [p.strip() for p in el.get("srcset", "").split(",") if p.strip()]
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
        return urls, [False] * len(urls)

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
