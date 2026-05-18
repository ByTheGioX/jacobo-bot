"""
Clasifica fotos de propiedades por tipo de estancia usando OpenRouter vision.
Selecciona la mejor variedad posible: fachada, salon, cocina, dormitorio, bano, terraza.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config.settings import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ROOM_TYPES = ["fachada", "salon", "cocina", "dormitorio", "bano", "terraza", "exterior", "otro"]

_CLASSIFY_PROMPT = (
    "Classify this real estate photo. Reply with ONLY one word from: "
    "fachada, salon, cocina, dormitorio, bano, terraza, exterior, otro. "
    "fachada=building exterior/facade. salon=living/dining room. cocina=kitchen. "
    "dormitorio=bedroom. bano=bathroom. terraza=terrace/balcony/patio. "
    "exterior=pool/garden/outside. otro=floor plan/corridor/unclear."
)

# Cuántas fotos conservar por tipo de estancia
_ROOM_QUOTA = {
    "fachada":    2,
    "salon":      4,
    "cocina":     3,
    "dormitorio": 4,
    "bano":       3,
    "terraza":    4,
    "exterior":   3,
    "otro":       0,
}

_IDEALISTA_LABEL_MAP = {
    "salon": "salon", "salón": "salon", "living": "salon", "comedor": "salon",
    "cocina": "cocina", "kitchen": "cocina",
    "dormitorio": "dormitorio", "habitacion": "dormitorio", "habitación": "dormitorio",
    "baño": "bano", "bano": "bano", "aseo": "bano",
    "terraza": "terraza", "balcón": "terraza", "balcon": "terraza", "patio": "terraza",
    "jardín": "exterior", "jardin": "exterior", "garden": "exterior", "piscina": "exterior",
    "fachada": "fachada", "exterior": "fachada",
    "garaje": "otro", "parking": "otro", "plano": "otro",
}


def _label_from_idealista(label: str) -> str | None:
    low = label.lower()
    for key, room_type in sorted(_IDEALISTA_LABEL_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if key in low:
            return room_type
    return None


def _classify_one(image_url: str) -> str:
    try:
        resp = requests.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": _CLASSIFY_PROMPT},
                    ],
                }],
                "max_tokens": 10,
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip().lower()
        for rt in _ROOM_TYPES:
            if rt in text:
                return rt
        return "otro"
    except Exception as e:
        logger.warning("Error clasificando foto %s: %s", image_url[:80], e)
        return "otro"


def select_best_photos(
    photo_urls: list[str],
    is_floor_plan_flags: list[bool],
    photo_labels: list[str] | None = None,
    max_photos: int = 10,
) -> list[tuple[str, bool]]:
    """
    Clasifica fotos y selecciona la mejor variedad de estancias.
    Devuelve lista de (url, is_floor_plan).
    """
    regular = [
        (url, flag) for url, flag in zip(photo_urls, is_floor_plan_flags)
        if not flag
    ]

    if not regular:
        return []

    if not OPENROUTER_API_KEY:
        logger.info("Sin OpenRouter — primeras %d fotos sin clasificar", max_photos)
        return regular[:max_photos]

    url_to_label = dict(zip(photo_urls, photo_labels)) if photo_labels else {}

    # Usar labels de Idealista cuando existan, clasificar el resto con IA
    url_to_type: dict[str, str] = {}
    to_classify: list[str] = []

    for url, _ in regular:
        label = url_to_label.get(url, "")
        detected = _label_from_idealista(label) if label else None
        if detected:
            url_to_type[url] = detected
        else:
            to_classify.append(url)

    if to_classify:
        logger.info("Clasificando %d fotos con OpenRouter vision...", len(to_classify))
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_classify_one, url): url for url in to_classify}
            for future in as_completed(futures):
                url_to_type[futures[future]] = future.result()

    # Agrupar por tipo en orden original
    buckets: dict[str, list[str]] = {rt: [] for rt in _ROOM_TYPES}
    for url, _ in regular:
        buckets[url_to_type.get(url, "otro")].append(url)

    # Seleccionar según cuota
    selected: list[str] = []
    for rt in _ROOM_TYPES:
        quota = _ROOM_QUOTA.get(rt, 0)
        selected.extend(buckets[rt][:quota])

    summary = {rt: len([u for u in selected if url_to_type.get(u) == rt])
               for rt in _ROOM_TYPES if _ROOM_QUOTA.get(rt, 0) > 0}
    logger.info("Fotos seleccionadas por estancia: %s (total: %d)", summary, len(selected))

    return [(url, False) for url in selected]
