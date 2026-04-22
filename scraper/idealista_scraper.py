"""
Scraper para perfiles de agencias en Idealista.
Usa Playwright (navegador real headless) para evitar bloqueos 403.

Para añadir nuevos perfiles, edita IDEALISTA_PROFILE_URLS en .env
"""

import json
import re
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config.settings import IDEALISTA_PROFILE_URLS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.idealista.com"


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
    def __init__(self, delay_range: tuple[float, float] = (2.0, 5.0), headless: bool = True):
        self.delay_range = delay_range
        self.headless = headless
        self._browser = None
        self._page = None

    def _sleep(self):
        time.sleep(random.uniform(*self.delay_range))

    def _start_browser(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            viewport={"width": 1366, "height": 768},
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        self._page = context.new_page()

    def _stop_browser(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _get_html(self, url: str, retries: int = 3) -> Optional[str]:
        for attempt in range(retries):
            try:
                self._page.goto(url, wait_until="networkidle", timeout=45000)
                # Esperar a que el JS renderice el contenido
                self._page.wait_for_timeout(random.randint(2000, 4000))
                html = self._page.content()
                if html and len(html) > 5000:
                    return html
                if attempt < retries - 1:
                    logger.warning("Página incompleta en %s, reintentando...", url)
                    time.sleep(5)
            except Exception as e:
                logger.error("Error cargando %s (intento %d): %s", url, attempt + 1, e)
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
        return None

    def _get_soup(self, url: str) -> Optional[BeautifulSoup]:
        html = self._get_html(url)
        if html is None:
            return None
        return BeautifulSoup(html, "lxml")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def scrape_profile(self, profile_url: str) -> list[Property]:
        """Extrae todas las propiedades de un perfil de agencia."""
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
                logger.info("Sin más propiedades en página %d. Total: %d", page, len(properties))
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
        """Scrapea todos los perfiles configurados en IDEALISTA_PROFILE_URLS del .env."""
        if not IDEALISTA_PROFILE_URLS:
            logger.warning("IDEALISTA_PROFILE_URLS está vacío en .env")
            return []

        self._start_browser()
        try:
            all_props: list[Property] = []
            for url in IDEALISTA_PROFILE_URLS:
                logger.info("--- Perfil: %s ---", url)
                all_props.extend(self.scrape_profile(url))
            return all_props
        finally:
            self._stop_browser()

    # ------------------------------------------------------------------
    # Paginación
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_page_url(profile_url: str, page: int) -> str:
        url = profile_url.rstrip("/")
        if page == 1:
            return url
        return f"{url}/pagina-{page}.htm"

    @staticmethod
    def _has_next_page(soup: BeautifulSoup) -> bool:
        if soup.find("link", {"rel": "next"}):
            return True
        return bool(soup.select_one("a.icon-arrow-right-after"))

    # ------------------------------------------------------------------
    # Página de listado → URLs de propiedades
    # ------------------------------------------------------------------

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[str]:
        # Extraer URLs directamente del navegador con JS — más fiable que parsear HTML
        try:
            hrefs: list[str] = self._page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href]'));
                    return links
                        .map(a => a.href)
                        .filter(h => h.includes('/inmueble/'));
                }
            """)
            seen: set[str] = set()
            urls: list[str] = []
            for href in hrefs:
                if href not in seen:
                    seen.add(href)
                    urls.append(href)
            if urls:
                logger.info("Encontradas %d propiedades en esta página", len(urls))
                return urls
        except Exception as e:
            logger.warning("JS eval falló, usando BeautifulSoup: %s", e)

        # Fallback: BeautifulSoup
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
    # Página de propiedad individual
    # ------------------------------------------------------------------

    def _scrape_property(self, url: str) -> Optional[Property]:
        logger.info("  → Propiedad: %s", url)
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
    # Extracción de fotos — 3 estrategias con fallback
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
                    images = json.loads(m.group(1))
                    for img in images:
                        if not isinstance(img, dict):
                            continue
                        img_url = _normalize_image_url(img.get("url", ""))
                        tag = (img.get("tag") or img.get("type") or "").lower()
                        if img_url:
                            urls.append(img_url)
                            is_plan.append("plano" in tag or "floor" in tag)
                    if urls:
                        return urls, is_plan
                except (json.JSONDecodeError, TypeError):
                    pass

            m = re.search(r'"photos"\s*:\s*(\[(?:"[^"]+",?\s*)+\])', text)
            if m:
                try:
                    photo_list = json.loads(m.group(1))
                    for img_url in photo_list:
                        if isinstance(img_url, str) and img_url.startswith("http"):
                            urls.append(_normalize_image_url(img_url))
                            is_plan.append(False)
                    if urls:
                        return urls, is_plan
                except (json.JSONDecodeError, TypeError):
                    pass

        return [], []

    @staticmethod
    def _photos_from_html_gallery(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls: list[str] = []
        is_plan: list[bool] = []

        for selector in [
            "picture.gallery-image source",
            "img.gallery-image",
            "div.gallery img",
            "div[class*='gallery'] img",
            "li.step img",
        ]:
            for el in soup.select(selector):
                src = ""
                if el.name == "source":
                    srcset = el.get("srcset", "")
                    parts = [p.strip() for p in srcset.split(",") if p.strip()]
                    src = parts[-1].split(" ")[0] if parts else ""
                else:
                    src = el.get("src") or el.get("data-src") or ""
                src = _normalize_image_url(src)
                alt = (el.get("alt") or "").lower()
                if src and src.startswith("http"):
                    urls.append(src)
                    is_plan.append("plano" in alt or "floor" in alt)
            if urls:
                break

        for img in soup.select("img[alt*='plano'], img[alt*='Plano'], img.floor-plan"):
            src = _normalize_image_url(img.get("src", ""))
            if src and src not in urls:
                urls.append(src)
                is_plan.append(True)

        return urls, is_plan

    @staticmethod
    def _photos_from_jsonld(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        urls: list[str] = []
        is_plan: list[bool] = []
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                images = data.get("image", [])
                if isinstance(images, str):
                    images = [images]
                for img in images:
                    if isinstance(img, dict):
                        img = img.get("url", "")
                    if isinstance(img, str) and img:
                        urls.append(_normalize_image_url(img))
                        is_plan.append(False)
            except (json.JSONDecodeError, AttributeError):
                continue
        return urls, is_plan

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
            "div.details-property-feature-one ul li",
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
                m = re.search(r"(\d+)\s*(?:dormitorio|habitaci[oó]n|cuarto)", lower)
                if m:
                    prop.rooms = int(m.group(1))
            if not prop.bathrooms:
                m = re.search(r"(\d+)\s*ba[ñn]o", lower)
                if m:
                    prop.bathrooms = int(m.group(1))
            if "parking" in lower or "garaje" in lower or "plaza de" in lower:
                prop.has_parking = True
            if "piscina" in lower:
                prop.has_pool = True
            if "terraza" in lower or "balc" in lower:
                prop.has_terrace = True
            if not prop.floor:
                m = re.search(r"planta\s+(\w+)", lower)
                if m:
                    prop.floor = m.group(1)

    @staticmethod
    def _detect_property_type(soup: BeautifulSoup) -> str:
        breadcrumb = soup.select("ol.breadcrumb li, nav[aria-label*='breadcrumb'] li")
        texts = [li.get_text(strip=True).lower() for li in breadcrumb]
        types = ("piso", "casa", "chalet", "apartamento", "estudio", "ático",
                 "attico", "local", "oficina", "garaje", "terreno", "villa")
        for t in texts:
            for pt in types:
                if pt in t:
                    return pt
        title = (soup.select_one("h1") or BeautifulSoup("", "lxml")).get_text().lower()
        for pt in types:
            if pt in title:
                return pt
        return "propiedad"

    @staticmethod
    def _detect_operation_type(url: str) -> str:
        if "/alquiler/" in url:
            return "rent"
        return "sale"


# ------------------------------------------------------------------
# Utilidad
# ------------------------------------------------------------------

def _normalize_image_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"/(?:WEB_DETAIL|WEB_LISTING|DETAIL|LISTING)-[A-Z0-9\-]+/", "/", url)
    url = url.split("?")[0]
    return url
