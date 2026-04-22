"""
Búsqueda inteligente de propiedades.
El comprador escribe en lenguaje natural y el sistema:
1. Parsea los requisitos con Claude AI
2. Busca en la DB de propiedades activas
3. Si no hay resultados, devuelve trigger para enviar emails a agencias
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from database.db import Database

logger = logging.getLogger(__name__)


@dataclass
class SearchCriteria:
    raw_query: str
    location: str = ""
    zones: list[str] = field(default_factory=list)
    property_type: str = ""
    operation: str = "sale"       # sale | rent
    rooms_min: Optional[int] = None
    rooms_max: Optional[int] = None
    price_max: Optional[int] = None
    area_min: Optional[float] = None
    has_parking: bool = False
    has_pool: bool = False
    has_terrace: bool = False
    contact_email: str = ""
    contact_name: str = ""

    def to_dict(self) -> dict:
        return {
            "location": self.location,
            "zones": self.zones,
            "property_type": self.property_type,
            "operation": self.operation,
            "rooms_min": self.rooms_min,
            "rooms_max": self.rooms_max,
            "price_max": self.price_max,
            "area_min": self.area_min,
            "has_parking": self.has_parking,
            "has_pool": self.has_pool,
            "has_terrace": self.has_terrace,
        }


class SmartSearch:
    """
    Parsea búsquedas en lenguaje natural.
    Primero intenta con Claude AI; si no hay API key, usa regex.
    """

    def __init__(self):
        self.db = Database()
        self._claude = None
        self._init_claude()

    def _init_claude(self):
        from config.settings import ANTHROPIC_API_KEY
        if ANTHROPIC_API_KEY:
            try:
                import anthropic
                self._claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            except ImportError:
                logger.warning("Librería anthropic no instalada. Usando parser regex.")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def process_query(
        self,
        query: str,
        contact_email: str = "",
        contact_name: str = "",
    ) -> dict:
        """
        Procesa una búsqueda de comprador.
        Retorna:
          {
            "matches": [...propiedades encontradas en la DB...],
            "criteria": {...criterios parseados...},
            "search_id": int,
            "needs_agency_email": bool,
          }
        """
        criteria = self._parse_query(query)
        criteria.contact_email = contact_email
        criteria.contact_name = contact_name

        search_id = self.db.save_buyer_search(
            raw_query=query,
            parsed=criteria.to_dict(),
            contact_email=contact_email,
            contact_name=contact_name,
        )

        matches = self._search_db(criteria)

        return {
            "matches": matches,
            "criteria": criteria.to_dict(),
            "search_id": search_id,
            "needs_agency_email": len(matches) == 0,
        }

    # ------------------------------------------------------------------
    # Parseo de la query
    # ------------------------------------------------------------------

    def _parse_query(self, query: str) -> SearchCriteria:
        if self._claude:
            return self._parse_with_claude(query)
        return self._parse_with_regex(query)

    def _parse_with_claude(self, query: str) -> SearchCriteria:
        prompt = f"""Extrae los criterios de búsqueda de esta descripción de propiedad inmobiliaria en España.
Devuelve SOLO un JSON con estos campos (usa null si no se menciona):
{{
  "location": "ciudad o zona",
  "zones": ["zona1", "zona2"],
  "property_type": "apartamento|piso|casa|chalet|estudio|local|garaje|terreno",
  "operation": "sale|rent",
  "rooms_min": número,
  "rooms_max": número,
  "price_max": número en euros,
  "area_min": número en m2,
  "has_parking": true|false,
  "has_pool": true|false,
  "has_terrace": true|false
}}

Búsqueda: "{query}"
"""
        try:
            msg = self._claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            # Extraer JSON del texto
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                return self._dict_to_criteria(query, data)
        except Exception as e:
            logger.error(f"Error con Claude AI: {e}")

        return self._parse_with_regex(query)

    @staticmethod
    def _parse_with_regex(query: str) -> SearchCriteria:
        q = query.lower()
        criteria = SearchCriteria(raw_query=query)

        # Localización
        loc_match = re.search(
            r"(?:en|por|cerca de|zona)\s+([A-Za-záéíóúñÁÉÍÓÚÑ\s]+?)(?:\s+de|\s+con|,|$)",
            query,
            re.IGNORECASE,
        )
        if loc_match:
            criteria.location = loc_match.group(1).strip()

        # Habitaciones
        rooms_match = re.search(r"(\d+)\s*(?:dormitorio|habitaci[oó]n|cuarto)", q)
        if rooms_match:
            criteria.rooms_min = int(rooms_match.group(1))

        # Precio máximo
        price_match = re.search(r"(?:hasta|máximo|max\.?|menos de)\s*(\d[\d\.]*)\s*(?:€|euros?)?", q)
        if price_match:
            criteria.price_max = int(re.sub(r"\.", "", price_match.group(1)))

        # Tipo
        for pt in ("apartamento", "piso", "casa", "chalet", "estudio", "ático", "local"):
            if pt in q:
                criteria.property_type = pt
                break

        # Características
        criteria.has_parking = any(w in q for w in ("parking", "garaje", "plaza"))
        criteria.has_pool = "piscina" in q
        criteria.has_terrace = any(w in q for w in ("terraza", "balcón", "balcon"))

        # Operación
        if any(w in q for w in ("alquiler", "alquilar", "arrendar", "renta")):
            criteria.operation = "rent"

        return criteria

    @staticmethod
    def _dict_to_criteria(query: str, data: dict) -> SearchCriteria:
        return SearchCriteria(
            raw_query=query,
            location=data.get("location") or "",
            zones=data.get("zones") or [],
            property_type=data.get("property_type") or "",
            operation=data.get("operation") or "sale",
            rooms_min=data.get("rooms_min"),
            rooms_max=data.get("rooms_max"),
            price_max=data.get("price_max"),
            area_min=data.get("area_min"),
            has_parking=bool(data.get("has_parking")),
            has_pool=bool(data.get("has_pool")),
            has_terrace=bool(data.get("has_terrace")),
        )

    # ------------------------------------------------------------------
    # Búsqueda en la DB
    # ------------------------------------------------------------------

    def _search_db(self, criteria: SearchCriteria) -> list[dict]:
        props = self.db.get_active_properties()
        results = []

        for prop in props:
            if not self._matches(prop, criteria):
                continue
            results.append({
                "idealista_id": prop["idealista_id"],
                "title": prop["title"],
                "price": prop["price"],
                "location": prop["location"],
                "rooms": prop["rooms"],
                "area_m2": prop["area_m2"],
                "has_parking": prop["has_parking"],
                "wp_post_id": prop["wp_post_id"],
            })

        return results

    @staticmethod
    def _matches(prop: dict, c: SearchCriteria) -> bool:
        # Operación
        if c.operation and prop.get("operation_type") != c.operation:
            return False

        # Habitaciones
        if c.rooms_min and (prop.get("rooms") or 0) < c.rooms_min:
            return False
        if c.rooms_max and (prop.get("rooms") or 0) > c.rooms_max:
            return False

        # Precio
        if c.price_max and prop.get("price") and prop["price"] > c.price_max:
            return False

        # Localización (búsqueda flexible)
        if c.location:
            loc = (prop.get("location") or "").lower()
            if c.location.lower() not in loc:
                return False

        # Parking
        if c.has_parking and not prop.get("has_parking"):
            return False

        # Piscina
        if c.has_pool and not prop.get("has_pool"):
            return False

        return True
