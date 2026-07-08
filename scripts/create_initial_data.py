"""Seed data awal INTERMILAN.

Jalankan setelah `python manage.py migrate` jika ingin membuat template checklist
dasar. Script ini idempotent untuk nama dokumen yang sama.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.development")

import django  # noqa: E402

django.setup()

from apps.documents.models import ChecklistTemplate  # noqa: E402


DEFAULT_CHECKLISTS = [
    ("SPM", "SPM", True, 10),
    ("SP2D", "SP2D", True, 20),
    ("DRPP", "DRPP", True, 30),
    ("Kuitansi", "KW", True, 40),
    ("Bukti Pendukung", "Dokumen Pendukung", False, 50),
]


def main():
    created = 0
    for nama_dokumen, kategori, wajib, urutan in DEFAULT_CHECKLISTS:
        _, was_created = ChecklistTemplate.objects.get_or_create(
            nama_dokumen=nama_dokumen,
            defaults={
                "kategori": kategori,
                "wajib": wajib,
                "urutan": urutan,
                "is_active": True,
            },
        )
        created += int(was_created)
    print(f"Seed selesai. Template baru dibuat: {created}")


if __name__ == "__main__":
    main()
