"""
Scraper para perfiles de agencias en Idealista.
Extrae listados de propiedades de URLs de perfil configuradas.
"""

import re
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config.settings import SCRAPER_HEADERS, IDEALISTA_PROFILE_URLS

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
    has_parking: bool = False
    has_pool: bool = False
    has_terrace: bool = False
    is_floor_plan: list[bool] = field(default_factory=list)  # por índice de foto
    property_type: str = ""
    operation_type: str = "sale"
    raw_features: dict = field(default_factory=dict)


class IdealistaScraper:
    def __init__(self, delay_range: tuple[float, float] = (2.0, 5.0)):
        self.session = requests.Session()
        self.session.headers.update(SCRAPER_HEADERS)
        self.delay_range = delay_range

    def _sleep(self):
        time.sleep(random.uniform(*self.delay_range))

    def _get(self, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 200:
                    return BeautifulSoup(resp.text, "lxml")
                if resp.status_code == 429:
                    logger.warning("Rate limited (429). Esperando 60s...")
                    time.sleep(60)
                elif resp.status_code == 403:
                    logger.warning(f"403 en {url}. Idealista puede haber bloqueado la IP.")
                    return None
                else:
                    logger.warning(f"HTTP {resp.status_code} en {url}")
            except requests.RequestException as e:
                logger.error(f"Error de red en {url}: {e}")
                wait = 5 * (attempt + 1)
                time.sleep(wait)
        return None

    # ------------------------------------------------------------------
    # Perfil de agencia → páginas de listados
    # ------------------------------------------------------------------

    def scrape_profile(self, profile_url: str) -> list[Property]:
        """Extrae todas las propiedades de un perfil de agencia."""
        logger.info(f"Scrapeando perfil: {profile_url}")
        properties: list[Property] = []
        page = 1

        while True:
            url = self._profile_page_url(profile_url, page)
            soup = self._get(url)
            if soup is None:
                break

            items = self._parse_listing_page(soup)
            if not items:
                logger.info(f"Sin más propiedades en página {page}. Total: {len(properties)}")
                break

            for item_url in items:
                prop = self._scrape_property(item_url)
                if prop:
                    properties.append(prop)
                self._sleep()

            # Comprobar si hay página siguiente
            if not self._has_next_page(soup):
                break

            page += 1
            self._sleep()

        return properties

    def scrape_all_profiles(self) -> list[Property]:
        """Scrapea todos los perfiles configurados en settings."""
        all_props: list[Property] = []
        for url in IDEALISTA_PROFILE_URLS:
            all_props.extend(self.scrape_profile(url))
        return all_props

    # ------------------------------------------------------------------
    # Helpers de paginación
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_page_url(profile_url: str, page: int) -> str:
        url = profile_url.rstrip("/")
        if page == 1:
            return url
        # Idealista usa /pagina-N.htm
        return f"{url}/pagina-{page}.htm"

    @staticmethod
    def _has_next_page(soup: BeautifulSoup) -> bool:
        return bool(soup.select_one("a.icon-arrow-right-after"))

    # ------------------------------------------------------------------
    # Página de listado → URLs de propiedades individuales
    # ------------------------------------------------------------------

    def _parse_listing_page(self, soup: BeautifulSoup) -> list[str]:
        urls = []
        for article in soup.select("article.item"):
            link = article.select_one("a.item-link")
            if link and link.get("href"):
                href = link["href"]
                if not href.startswith("http"):
                    href = urljoin(BASE_URL, href)
                urls.append(href)
        return urls

    # ------------------------------------------------------------------
    # Página de propiedad individual
    # ------------------------------------------------------------------

    def _scrape_property(self, url: str) -> Optional[Property]:
        logger.info(f"  → Propiedad: {url}")
        soup = self._get(url)
        if soup is None:
            return None

        prop_id = self._extract_id(url)
        if not prop_id:
            return None

        prop = Property(
            idealista_id=prop_id,
            url=url,
            title=self._text(soup, "h1.main-info__title"),
            price_text=self._text(soup, "span.info-data-price"),
            location=self._text(soup, "span.main-info__title-minor"),
            description=self._text(soup, "div.comment"),
            property_type=self._detect_property_type(soup),
            operation_type=self._detect_operation_type(url),
        )

        prop.price = self._parse_price(prop.price_text)
        self._parse_features(soup, prop)
        prop.photo_urls, prop.is_floor_plan = self._extract_photos(soup)

        return prop

    # ------------------------------------------------------------------
    # Parsers auxiliares
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_id(url: str) -> Optional[str]:
        # Idealista URLs: /inmueble/12345678/
        m = re.search(r"/inmueble/(\d+)/", url)
        return m.group(1) if m else None

    @staticmethod
    def _text(soup: BeautifulSoup, selector: str) -> str:
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else ""

    @staticmethod
    def _parse_price(text: str) -> Optional[int]:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None

    def _parse_features(self, soup: BeautifulSoup, prop: Property):
        features_ul = soup.select("div.details-property_features ul li")
        for li in features_ul:
            text = li.get_text(strip=True).lower()

            m = re.search(r"(\d+)\s*m²", text)
            if m:
                prop.area_m2 = float(m.group(1))

            m = re.search(r"(\d+)\s*hab", text)
            if m:
                prop.rooms = int(m.group(1))

            m = re.search(r"(\d+)\s*baño", text)
            if m:
                prop.bathrooms = int(m.group(1))

            if "parking" in text or "garaje" in text:
                prop.has_parking = True
            if "piscina" in text:
                prop.has_pool = True
            if "terraza" in text or "balcón" in text:
                prop.has_terrace = True

            floor_match = re.search(r"planta\s+(\w+)", text)
            if floor_match:
                prop.floor = floor_match.group(1)

    @staticmethod
    def _detect_property_type(soup: BeautifulSoup) -> str:
        breadcrumb = soup.select("ol.breadcrumb li")
        texts = [li.get_text(strip=True).lower() for li in breadcrumb]
        for t in texts:
            for pt in ("piso", "casa", "chalet", "apartamento", "estudio", "ático", "local", "oficina", "garaje", "terreno"):
                if pt in t:
                    return pt
        return "propiedad"

    @staticmethod
    def _detect_operation_type(url: str) -> str:
        if "/alquiler/" in url:
            return "rent"
        return "sale"

    def _extract_photos(self, soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        """
        Devuelve (lista_de_urls, lista_de_booleans).
        El booleano indica si la foto es un plano (floor plan).
        """
        urls: list[str] = []
        is_plan: list[bool] = []

        # Galería principal
        for img in soup.select("picture.gallery-image source[srcset], img.gallery-image"):
            src = img.get("srcset") or img.get("src") or ""
            # Tomar la URL de mayor resolución si hay srcset
            src = src.split(",")[-1].strip().split(" ")[0]
            if src and src.startswith("http"):
                urls.append(src)
                is_plan.append(False)

        # Planos (suelen estar en sección separada o con alt="plano")
        for img in soup.select("img[alt*='plano'], img[alt*='Plano'], img.floor-plan"):
            src = img.get("src", "")
            if src and src not in urls:
                urls.append(src)
                is_plan.append(True)

        # Fallback: buscar imágenes en el JSON-LD de la página
        if not urls:
            urls, is_plan = self._extract_photos_from_jsonld(soup)

        return urls, is_plan

    @staticmethod
    def _extract_photos_from_jsonld(soup: BeautifulSoup) -> tuple[list[str], list[bool]]:
        import json
        urls = []
        is_plan = []
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string or "")
                images = data.get("image", [])
                if isinstance(images, str):
                    images = [images]
                for img in images:
                    if isinstance(img, dict):
                        img = img.get("url", "")
                    if img:
                        urls.append(img)
                        is_plan.append(False)
            except (json.JSONDecodeError, AttributeError):
                continue
        return urls, is_plan
