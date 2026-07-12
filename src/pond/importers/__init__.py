"""Importer package: registers all importers on import."""

from pond.importers import bank, spotify, takeout, whatsapp  # noqa: F401
from pond.importers.base import REGISTRY, BaseImporter, ImportStats, detect_importers

__all__ = ["REGISTRY", "BaseImporter", "ImportStats", "detect_importers"]
