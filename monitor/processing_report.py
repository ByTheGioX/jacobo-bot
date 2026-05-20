"""
Reporter centralizado para auditoría de cada ciclo.

Genera dos artefactos:
  - data/processing_report.log    → log estructurado por propiedad (legible humano)
  - data/processing_report.jsonl  → una línea JSON por propiedad (para parseo automático)

Cada propiedad reporta:
  scraped, selected, sent_to_kie, kie_ok, kie_failed, uploaded_wp, skipped (+ motivos)
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPORT_LOG = Path("data/processing_report.log")
_REPORT_JSONL = Path("data/processing_report.jsonl")


@dataclass
class PropertyReport:
    idealista_id: str
    title: str = ""
    url: str = ""
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    # Conteos
    photos_scraped: int = 0
    photos_selected: int = 0
    photos_sent_to_kie: int = 0
    photos_kie_ok: int = 0
    photos_kie_failed: int = 0
    photos_uploaded_wp: int = 0
    photos_dedupe_removed: int = 0
    photos_home_staging: int = 0
    # Acciones
    wp_action: str = ""   # "created" | "updated" | "skipped" | "aborted"
    wp_post_id: Optional[int] = None
    # Motivos por foto (lista de strings legibles)
    skip_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    finished_at: str = ""

    def note(self, message: str):
        self.notes.append(message)

    def skip_reason(self, idx: int, motivo: str):
        self.skip_reasons.append(f"foto {idx}: {motivo}")


class CycleReporter:
    """Recolecta PropertyReport durante un ciclo y los vuelca al final."""

    def __init__(self):
        self.reports: list[PropertyReport] = []
        self.cycle_started = datetime.utcnow().isoformat()
        _REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)

    def new_property(self, idealista_id: str, title: str = "", url: str = "") -> PropertyReport:
        rep = PropertyReport(idealista_id=idealista_id, title=title[:80], url=url)
        self.reports.append(rep)
        return rep

    def flush(self):
        """Escribe el resumen del ciclo a los archivos de log."""
        for rep in self.reports:
            if not rep.finished_at:
                rep.finished_at = datetime.utcnow().isoformat()

        # JSONL (append) — una línea por propiedad
        with open(_REPORT_JSONL, "a", encoding="utf-8") as f:
            for rep in self.reports:
                f.write(json.dumps(asdict(rep), ensure_ascii=False) + "\n")

        # Log humano-legible (append)
        with open(_REPORT_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*70}\n")
            f.write(f"CICLO {self.cycle_started} → {datetime.utcnow().isoformat()}\n")
            f.write(f"Propiedades procesadas: {len(self.reports)}\n")
            f.write(f"{'='*70}\n\n")

            for rep in self.reports:
                f.write(f"[{rep.idealista_id}] {rep.title}\n")
                f.write(f"  WP: {rep.wp_action.upper()}")
                if rep.wp_post_id:
                    f.write(f" (post {rep.wp_post_id})")
                f.write("\n")
                f.write(f"  Fotos: scraped={rep.photos_scraped} → selected={rep.photos_selected}")
                if rep.photos_dedupe_removed:
                    f.write(f" (-{rep.photos_dedupe_removed} dup)")
                f.write(f" → kie_sent={rep.photos_sent_to_kie}")
                f.write(f" → ok={rep.photos_kie_ok} fail={rep.photos_kie_failed}")
                f.write(f" → uploaded={rep.photos_uploaded_wp}")
                if rep.photos_home_staging:
                    f.write(f" | home_staging={rep.photos_home_staging}")
                f.write("\n")
                for reason in rep.skip_reasons:
                    f.write(f"    ✗ {reason}\n")
                for note in rep.notes:
                    f.write(f"    · {note}\n")
                f.write("\n")

            # Totales del ciclo
            totals = self._totals()
            f.write(f"--- TOTALES DEL CICLO ---\n")
            for k, v in totals.items():
                f.write(f"  {k}: {v}\n")
            f.write("\n")

        # También al log normal — resumen corto
        totals = self._totals()
        logger.info("=== Reporte de ciclo === %s", totals)
        logger.info("Detalle completo en %s y %s", _REPORT_LOG, _REPORT_JSONL)

    def _totals(self) -> dict:
        return {
            "propiedades": len(self.reports),
            "wp_created": sum(1 for r in self.reports if r.wp_action == "created"),
            "wp_updated": sum(1 for r in self.reports if r.wp_action == "updated"),
            "wp_aborted": sum(1 for r in self.reports if r.wp_action == "aborted"),
            "wp_skipped": sum(1 for r in self.reports if r.wp_action == "skipped"),
            "fotos_subidas": sum(r.photos_uploaded_wp for r in self.reports),
            "fotos_kie_fail": sum(r.photos_kie_failed for r in self.reports),
            "fotos_home_staging": sum(r.photos_home_staging for r in self.reports),
        }
