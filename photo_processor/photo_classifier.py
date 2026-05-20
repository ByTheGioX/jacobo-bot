"""
Clasifica fotos de propiedades por tipo de estancia usando OpenRouter vision.
Selecciona la mejor variedad posible: fachada, salon, cocina, dormitorio, bano, terraza.

Por cada foto detecta:
  - room_type: tipo de estancia (fachada, salon, cocina, dormitorio, bano, terraza, exterior, otro)
  - empty: True si está vacía (sin muebles) → activa Home Staging automático
  - shot: "wide" (panorámica) o "close" (detalle/cerca). Wide se prefiere.

Después deduplica fotos casi-idénticas usando pHash (hamming distance ≤ 6).
"""

import hashlib
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config.settings import OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ROOM_TYPES = ["fachada", "salon", "cocina", "dormitorio", "bano", "terraza", "exterior", "otro"]

# Pedimos JSON estructurado: tipo, vacío, tipo-de-toma.
_CLASSIFY_PROMPT = (
    "Analyze this real estate photo. Respond ONLY with valid JSON, no other text. Schema:\n"
    '{"type": "<one of: fachada, salon, cocina, dormitorio, bano, terraza, exterior, otro>",\n'
    ' "empty": <true if room has no furniture, false otherwise>,\n'
    ' "shot": "<wide if it is a wide/panoramic full-room view, close if it is a detail/close-up>"}\n\n'
    "Type meanings: fachada=building facade. salon=living/dining room. cocina=kitchen. "
    "dormitorio=bedroom. bano=bathroom. terraza=terrace/balcony/patio. "
    "exterior=garden/pool/outdoor. otro=floor plan/corridor/unclear.\n"
    "An empty room has no sofa, table, bed, etc — bare walls and floor only."
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

# Umbral de hamming distance para considerar dos pHash como "casi idénticas" (0=idéntica, 64=opuesta).
_PHASH_DUP_THRESHOLD = 6


def _label_from_idealista(label: str) -> str | None:
    low = label.lower()
    for key, room_type in sorted(_IDEALISTA_LABEL_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if key in low:
            return room_type
    return None


def _parse_classification(text: str) -> dict:
    """Extrae JSON del texto del modelo aunque venga con markdown o ruido."""
    # Intentar parseo directo
    try:
        return json.loads(text)
    except Exception:
        pass
    # Buscar bloque JSON con regex
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def _classify_one(image_url: str) -> dict:
    """Devuelve {type, empty, shot} para una foto. Fallback: type=otro."""
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
                "max_tokens": 60,
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = _parse_classification(text)
        rtype = (parsed.get("type") or "otro").lower()
        if rtype not in _ROOM_TYPES:
            rtype = "otro"
        return {
            "type": rtype,
            "empty": bool(parsed.get("empty", False)),
            "shot": (parsed.get("shot") or "wide").lower(),
        }
    except Exception as e:
        logger.warning("Error clasificando foto %s: %s", image_url[:80], e)
        return {"type": "otro", "empty": False, "shot": "wide"}


def _compute_phash(image_url: str) -> str | None:
    """Descarga la imagen en memoria y calcula su pHash. Devuelve None si falla."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        return None
    try:
        resp = requests.get(image_url, timeout=20, stream=True)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        return str(imagehash.phash(img))
    except Exception as e:
        logger.debug("pHash fallido para %s: %s", image_url[:80], e)
        return None


def _hamming(h1: str, h2: str) -> int:
    """Distancia de Hamming entre dos pHash hex strings."""
    try:
        import imagehash
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64


def _dedupe_by_phash(urls_in_bucket: list[str], phashes: dict[str, str | None]) -> list[str]:
    """De una lista de URLs (mismo room_type), elimina las casi-idénticas. Conserva la primera."""
    kept: list[str] = []
    kept_hashes: list[str] = []
    for url in urls_in_bucket:
        h = phashes.get(url)
        if h is None:
            kept.append(url)
            continue
        is_dup = any(_hamming(h, kh) <= _PHASH_DUP_THRESHOLD for kh in kept_hashes)
        if is_dup:
            logger.debug("  [DUP] %s descartada (pHash similar)", url[:80])
            continue
        kept.append(url)
        kept_hashes.append(h)
    return kept


def select_best_photos(
    photo_urls: list[str],
    is_floor_plan_flags: list[bool],
    photo_labels: list[str] | None = None,
    max_photos: int = 10,
) -> list[dict]:
    """
    Clasifica + deduplica fotos. Devuelve lista de dicts con metadata:
        {url, room_type, empty, shot, is_floor_plan}

    Compatibilidad: el caller puede iterar sólo (url, is_floor_plan) ignorando los demás campos.
    """
    regular = [
        (url, flag) for url, flag in zip(photo_urls, is_floor_plan_flags)
        if not flag
    ]

    if not regular:
        return []

    # Sin OpenRouter: primeras N sin clasificar (modo simple)
    if not OPENROUTER_API_KEY:
        logger.info("Sin OpenRouter — primeras %d fotos sin clasificar", max_photos)
        return [
            {"url": url, "room_type": "otro", "empty": False, "shot": "wide", "is_floor_plan": False}
            for url, _ in regular[:max_photos]
        ]

    url_to_label = dict(zip(photo_urls, photo_labels)) if photo_labels else {}
    classifications: dict[str, dict] = {}
    to_classify: list[str] = []

    # Pre-clasificación rápida por label de Idealista (sin gastar tokens)
    for url, _ in regular:
        label = url_to_label.get(url, "")
        detected = _label_from_idealista(label) if label else None
        if detected and detected != "fachada":
            # Si tenemos label pero no sabemos vacío/wide, igual hay que preguntar a IA
            # Para fachadas confiamos en el label sin preguntar
            to_classify.append(url)
        elif detected == "fachada":
            classifications[url] = {"type": "fachada", "empty": False, "shot": "wide"}
        else:
            to_classify.append(url)

    if to_classify:
        logger.info("Clasificando %d fotos con OpenRouter vision (type+empty+shot)...", len(to_classify))
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_classify_one, url): url for url in to_classify}
            for future in as_completed(futures):
                classifications[futures[future]] = future.result()

    # Calcular pHash en paralelo para deduplicación
    logger.info("Calculando pHash de %d fotos para deduplicación...", len(regular))
    phashes: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_compute_phash, url): url for url, _ in regular}
        for future in as_completed(futures):
            phashes[futures[future]] = future.result()

    # Agrupar por room_type respetando orden original
    buckets: dict[str, list[str]] = {rt: [] for rt in _ROOM_TYPES}
    for url, _ in regular:
        rt = classifications.get(url, {}).get("type", "otro")
        buckets[rt].append(url)

    # Dentro de cada bucket: deduplicar pHash + preferir wide shots
    selected_urls: list[str] = []
    dup_counts: dict[str, int] = {}
    for rt in _ROOM_TYPES:
        quota = _ROOM_QUOTA.get(rt, 0)
        if quota == 0 or not buckets[rt]:
            continue
        deduped = _dedupe_by_phash(buckets[rt], phashes)
        dup_counts[rt] = len(buckets[rt]) - len(deduped)
        # Ordenar: wide primero, close después (estable, preserva orden original dentro de grupo)
        wide = [u for u in deduped if classifications.get(u, {}).get("shot") == "wide"]
        close = [u for u in deduped if classifications.get(u, {}).get("shot") != "wide"]
        ordered = wide + close
        selected_urls.extend(ordered[:quota])

    if any(v for v in dup_counts.values()):
        logger.info("Duplicados eliminados por pHash: %s",
                    {k: v for k, v in dup_counts.items() if v > 0})

    summary = {rt: sum(1 for u in selected_urls if classifications.get(u, {}).get("type") == rt)
               for rt in _ROOM_TYPES if _ROOM_QUOTA.get(rt, 0) > 0}
    empties = sum(1 for u in selected_urls if classifications.get(u, {}).get("empty"))
    logger.info("Fotos seleccionadas: %s | vacías: %d (total: %d)", summary, empties, len(selected_urls))

    return [
        {
            "url": url,
            "room_type": classifications.get(url, {}).get("type", "otro"),
            "empty": bool(classifications.get(url, {}).get("empty", False)),
            "shot": classifications.get(url, {}).get("shot", "wide"),
            "is_floor_plan": False,
        }
        for url in selected_urls
    ]
