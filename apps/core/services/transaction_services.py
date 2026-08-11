"""
Transaction Package Services.

Provides business logic for:
- Creating and enriching transaction packages
- SP2D and SPM enrichment
- Active parent management
- DRPP parent selection with conflict detection
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import (
    ActiveParentSession,
    DRPPPreviewState,
    TransactionPackage,
    TransactionProvenance,
)
from apps.accounts.access import get_profile

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """Result of an enrichment operation."""
    package: TransactionPackage
    created: bool
    updated: bool
    enriched_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParentSelectionResult:
    """Result of a parent selection operation."""
    package: Optional[TransactionPackage]
    selected: bool
    selection_method: str
    conflict: bool
    conflict_message: str = ""
    candidates: list[TransactionPackage] = field(default_factory=list)


def normalize_satker(value: str) -> str:
    """Normalize satker code to consistent format."""
    if not value:
        return ""
    # Remove common prefixes and whitespace
    value = str(value).strip().upper()
    # Handle "bps" prefix
    if value.startswith("BPS"):
        value = value[3:]
    # Handle "KK_" prefix from filenames
    if value.startswith("KK_"):
        value = value[3:]
    # Pad to 4 digits if numeric
    if value.isdigit():
        value = value.zfill(4)
    return value


def normalize_nomor_spm(value: str) -> str:
    """Normalize SPM number to consistent format."""
    if not value:
        return ""
    return str(value).strip().upper()


def normalize_tahun(value) -> int:
    """Normalize tahun to integer."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return 0


def find_or_create_package(
    satker_code: str,
    tahun: int,
    nomor_spm: str,
    user=None,
) -> tuple[TransactionPackage, bool]:
    """
    Find or create a transaction package by canonical identity.

    Returns:
        tuple: (package, created) - the package and whether it was newly created
    """
    satker_code = normalize_satker(satker_code)
    nomor_spm = normalize_nomor_spm(nomor_spm)
    tahun = normalize_tahun(tahun)

    if not all([satker_code, tahun, nomor_spm]):
        raise ValueError("satker_code, tahun, and nomor_spm are required")

    package, created = TransactionPackage.objects.get_or_create(
        satker_code=satker_code,
        tahun=tahun,
        nomor_spm=nomor_spm,
        defaults={"created_by": user} if user else {},
    )

    logger.info(
        "Package lookup: %s/%d/%s -> %s (created=%s)",
        satker_code, tahun, nomor_spm,
        package.pk, created
    )

    return package, created


def enrich_from_sp2d(
    package: TransactionPackage,
    no_sp2d: str,
    tanggal_sp2d: date = None,
    nilai_sp2d: Decimal = None,
    satker_name: str = None,
    source_filename: str = None,
    user=None,
    was_created: bool = False,
) -> EnrichmentResult:
    """
    Enrich a transaction package with SP2D data.

    This operation is idempotent - uploading the same SP2D data multiple times
    will not create duplicate packages or overwrite existing data unnecessarily.
    """
    created = was_created
    updated = False
    enriched_fields = []
    warnings = []

    # Check if this is actually updating an existing package
    if package.has_sp2d and package.no_sp2d == no_sp2d:
        logger.info("SP2D already present, no update needed: %s", package)
    else:
        # Update SP2D fields
        if no_sp2d and package.no_sp2d != no_sp2d:
            package.no_sp2d = no_sp2d
            enriched_fields.append("no_sp2d")

        if tanggal_sp2d and package.tanggal_sp2d != tanggal_sp2d:
            package.tanggal_sp2d = tanggal_sp2d
            enriched_fields.append("tanggal_sp2d")

        if nilai_sp2d is not None and package.nilai_sp2d != nilai_sp2d:
            package.nilai_sp2d = nilai_sp2d
            enriched_fields.append("nilai_sp2d")

        if source_filename and not package.sp2d_source:
            package.sp2d_source = source_filename
            enriched_fields.append("sp2d_source")

        package.has_sp2d = True
        updated = True

    # Update status
    if updated or created:
        package.update_status()
        package.save()

        # Record provenance
        if source_filename:
            TransactionProvenance.objects.create(
                transaction_package=package,
                source_type=TransactionProvenance.SourceType.SP2D,
                source_filename=source_filename,
                original_satker=package.satker_code,
                original_tahun=package.tahun,
                original_nomor_spm=package.nomor_spm,
            )

    return EnrichmentResult(
        package=package,
        created=created,
        updated=updated,
        enriched_fields=enriched_fields,
        warnings=warnings,
    )


def enrich_from_spm(
    package: TransactionPackage,
    tanggal_spm: date = None,
    jenis_spm: str = None,
    nilai_spm: Decimal = None,
    deskripsi: str = None,
    source_filename: str = None,
    user=None,
) -> EnrichmentResult:
    """
    Enrich a transaction package with SPM document data.

    This operation is idempotent - uploading the same SPM data multiple times
    will not overwrite existing data unnecessarily.
    """
    created = False
    updated = False
    enriched_fields = []
    warnings = []

    if not package.pk:
        package.created_by = user
        created = True

    # Update SPM fields
    if tanggal_spm and package.tanggal_spm != tanggal_spm:
        if package.tanggal_spm:
            warnings.append(
                f"Tanggal SPM sebelumnya ({package.tanggal_spm}) berbeda dengan input ({tanggal_spm})"
            )
        package.tanggal_spm = tanggal_spm
        enriched_fields.append("tanggal_spm")

    if jenis_spm and package.jenis_spm != jenis_spm:
        if package.jenis_spm:
            warnings.append(
                f"Jenis SPM sebelumnya ({package.jenis_spm}) berbeda dengan input ({jenis_spm})"
            )
        package.jenis_spm = jenis_spm
        enriched_fields.append("jenis_spm")

    if nilai_spm is not None and package.nilai_spm != nilai_spm:
        # Check for significant discrepancy
        if package.nilai_spm and abs(package.nilai_spm - nilai_spm) / package.nilai_spm > 0.01:
            warnings.append(
                f"Nilai SPM berbeda signifikan: DB={package.nilai_spm}, Input={nilai_spm}"
            )
        package.nilai_spm = nilai_spm
        enriched_fields.append("nilai_spm")

    if deskripsi and not package.deskripsi:
        package.deskripsi = deskripsi
        enriched_fields.append("deskripsi")

    if source_filename and not package.spm_source:
        package.spm_source = source_filename
        enriched_fields.append("spm_source")

    package.has_spm_document = True
    updated = True

    # Update status
    if updated or created:
        package.update_status()
        package.save()

        # Record provenance
        if source_filename:
            TransactionProvenance.objects.create(
                transaction_package=package,
                source_type=TransactionProvenance.SourceType.SPM,
                source_filename=source_filename,
                original_satker=package.satker_code,
                original_tahun=package.tahun,
                original_nomor_spm=package.nomor_spm,
            )

    return EnrichmentResult(
        package=package,
        created=created,
        updated=updated,
        enriched_fields=enriched_fields,
        warnings=warnings,
    )


def get_active_parent_for_user(request=None, user=None) -> Optional[ActiveParentSession]:
    """Get the active SPM parent for the current session/user."""
    if user is None and request:
        user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None

    return ActiveParentSession.objects.filter(
        user=user,
    ).select_related("transaction_package").order_by("-updated_at").first()


def set_active_parent(
    request=None,
    package: TransactionPackage = None,
    selection_method: str = "EXPLICIT",
    selection_evidence: dict = None,
    user=None,
) -> ActiveParentSession:
    """Set the active SPM parent for DRPP uploads."""
    # Handle positional argument: if first arg is TransactionPackage, it's the old signature
    if isinstance(request, TransactionPackage):
        package = request
        request = None

    if package is None:
        raise ValueError("package is required")

    if user is None and request:
        user = getattr(request, 'user', None)

    session_key = getattr(request, 'session', None) and getattr(request.session, 'session_key', '')

    parent_session, _ = ActiveParentSession.objects.update_or_create(
        user=user,
        defaults={
            "session_key": session_key or "",
            "transaction_package": package,
            "satker_code": package.satker_code,
            "tahun": package.tahun,
            "nomor_spm": package.nomor_spm,
            "tanggal_spm": package.tanggal_spm,
            "jenis_spm": package.jenis_spm,
            "selection_method": selection_method,
            "selection_evidence": selection_evidence or {},
        }
    )

    logger.info(
        "Active parent set: %s/%d/%s (method=%s)",
        package.satker_code, package.tahun, package.nomor_spm,
        selection_method
    )

    return parent_session


def clear_active_parent(request=None, user=None) -> bool:
    """Clear the active SPM parent."""
    if user is None and request:
        user = getattr(request, 'user', None)
    if not user:
        return False

    deleted, _ = ActiveParentSession.objects.filter(user=user).delete()
    if user and hasattr(user, 'username'):
        logger.info("Active parent cleared for user %s", user.username)
    return deleted > 0


def validate_parent_compatibility(
    package: TransactionPackage,
    drpp_satker: str = None,
    drpp_tahun: int = None,
    drpp_nomor_spm: str = None,
) -> tuple[bool, str]:
    """
    Validate that a package is compatible with DRPP evidence.

    Returns:
        tuple: (is_compatible, conflict_message)
    """
    conflicts = []

    # Normalize inputs
    drpp_satker = normalize_satker(drpp_satker) if drpp_satker else None
    drpp_tahun = normalize_tahun(drpp_tahun) if drpp_tahun else None
    drpp_nomor_spm = normalize_nomor_spm(drpp_nomor_spm) if drpp_nomor_spm else None

    # Check satker conflict - THIS IS CRITICAL
    if drpp_satker and package.satker_code != drpp_satker:
        conflicts.append(
            f"SPM parent aktif tidak cocok dengan DRPP ini. "
            f"Satker {package.satker_code} tidak cocok dengan {drpp_satker}."
        )

    # Check tahun conflict
    if drpp_tahun and package.tahun != drpp_tahun:
        conflicts.append(
            f"Tahun SPM ({package.tahun}) tidak cocok dengan DRPP ({drpp_tahun})."
        )

    # Check nomor_spm conflict
    if drpp_nomor_spm and package.nomor_spm != drpp_nomor_spm:
        # Only flag if satker also matches - otherwise it's expected
        if drpp_satker == package.satker_code:
            conflicts.append(
                f"Nomor SPM ({package.nomor_spm}) tidak cocok dengan DRPP ({drpp_nomor_spm})."
            )

    if conflicts:
        return False, " ".join(conflicts)

    return True, ""


def find_compatible_parent(
    drpp_satker: str = None,
    drpp_tahun: int = None,
    drpp_nomor_spm: str = None,
    active_parent: ActiveParentSession = None,
) -> ParentSelectionResult:
    """
    Find a compatible SPM parent for a DRPP.

    This implements the safety rules:
    1. Never guess when there's ambiguity
    2. Always respect satker/year/SPM identity
    3. Never override conflicts silently
    """
    # Normalize inputs
    drpp_satker = normalize_satker(drpp_satker) if drpp_satker else None
    drpp_tahun = normalize_tahun(drpp_tahun) if drpp_tahun else None
    drpp_nomor_spm = normalize_nomor_spm(drpp_nomor_spm) if drpp_nomor_spm else None

    # Build base query
    base_q = Q()

    if drpp_satker:
        base_q &= Q(satker_code=drpp_satker)
    if drpp_tahun:
        base_q &= Q(tahun=drpp_tahun)
    if drpp_nomor_spm:
        base_q &= Q(nomor_spm=drpp_nomor_spm)

    # Count candidates
    candidates = list(TransactionPackage.objects.filter(base_q).order_by("-tanggal_spm", "-created_at"))

    if not candidates:
        return ParentSelectionResult(
            package=None,
            selected=False,
            selection_method="NONE",
            conflict=False,
            candidates=[],
        )

    # Exact match with all three components
    if drpp_satker and drpp_tahun and drpp_nomor_spm:
        package = candidates[0]  # Should be only one with all three
        return ParentSelectionResult(
            package=package,
            selected=True,
            selection_method="EVIDENCE_MATCH",
            conflict=False,
            candidates=candidates,
        )

    # Match with two components - check for ambiguity
    provided_count = sum(1 for x in [drpp_satker, drpp_tahun, drpp_nomor_spm] if x)
    if provided_count >= 2 and len(candidates) == 1:
        package = candidates[0]
        return ParentSelectionResult(
            package=package,
            selected=True,
            selection_method="EVIDENCE_MATCH",
            conflict=False,
            candidates=candidates,
        )

    # Ambiguous - multiple candidates or insufficient evidence
    if len(candidates) > 1:
        return ParentSelectionResult(
            package=None,
            selected=False,
            selection_method="AMBIGUOUS",
            conflict=True,
            conflict_message=f"Ditemukan {len(candidates)} SPM candidate. Pilih yang benar.",
            candidates=candidates,
        )

    # Single candidate but insufficient evidence - use if compatible
    package = candidates[0]

    # Check if active parent matches the candidate
    if active_parent and active_parent.transaction_package_id == package.pk:
        # Validate compatibility
        is_compatible, conflict_msg = validate_parent_compatibility(
            package,
            drpp_satker=drpp_satker,
            drpp_tahun=drpp_tahun,
            drpp_nomor_spm=drpp_nomor_spm,
        )
        if is_compatible:
            return ParentSelectionResult(
                package=package,
                selected=True,
                selection_method="AUTO_COMPATIBLE",
                conflict=False,
                candidates=candidates,
            )

    # Single candidate, not from active parent - require confirmation
    return ParentSelectionResult(
        package=None,
        selected=False,
        selection_method="NEEDS_CONFIRMATION",
        conflict=True,
        conflict_message="Tidak ada parent aktif atau bukti tidak cukup. Pilih SPM parent secara manual.",
        candidates=candidates,
    )


def create_drpp_preview_state(
    request,
    nomor_drpp: str,
    satker_code: str,
    tahun: int,
    parent_package: TransactionPackage,
    preview_data: dict,
    conflict: bool = False,
    conflict_message: str = "",
    user=None,
) -> DRPPPreviewState:
    """
    Create a frozen preview state for DRPP commit.

    Once the parent is selected here, it will be used for commit
    regardless of any subsequent changes.
    """
    if user is None:
        user = request.user if request else None

    session_key = request.session.session_key if request else ""

    preview_state, _ = DRPPPreviewState.objects.update_or_create(
        session_key=session_key,
        user=user,
        nomor_drpp=normalize_nomor_spm(nomor_drpp),
        defaults={
            "satker_code": normalize_satker(satker_code),
            "tahun": normalize_tahun(tahun),
            "frozen_parent_package": parent_package,
            "frozen_satker_code": parent_package.satker_code,
            "frozen_tahun": parent_package.tahun,
            "frozen_nomor_spm": parent_package.nomor_spm,
            "preview_data": preview_data,
            "selection_conflict": conflict,
            "conflict_message": conflict_message,
            "status": DRPPPreviewState.Status.PENDING,
            "expires_at": timezone.now() + timezone.timedelta(hours=24),
        }
    )

    logger.info(
        "DRPP preview state created: %s -> %s/%d/%s (frozen)",
        nomor_drpp,
        parent_package.satker_code,
        parent_package.tahun,
        parent_package.nomor_spm,
    )

    return preview_state


def commit_drpp_with_preview(
    preview_state: DRPPPreviewState,
) -> tuple[bool, str, Optional[TransactionPackage]]:
    """
    Commit DRPP using the frozen preview state.

    This is the ONLY way to commit - using the frozen parent,
    not any dynamic queries.
    """
    # Get the frozen parent
    parent_package = preview_state.get_frozen_parent_for_commit()

    if not parent_package:
        return False, "Parent package tidak ditemukan atau sudah tidak valid.", None

    # Validate the frozen parent still exists and matches
    if not preview_state.is_frozen_parent_valid():
        return False, "Parent yang dipilih tidak lagi valid. Buat preview ulang.", None

    # Check for conflicts
    if preview_state.selection_conflict:
        return False, preview_state.conflict_message or "Terjadi konflik seleksi.", None

    with db_transaction.atomic():
        # Mark preview as committed
        preview_state.status = DRPPPreviewState.Status.COMMITTED
        preview_state.save()

        # Update package counters
        parent_package.has_drpp = True
        parent_package.drpp_count += 1
        parent_package.update_status()
        parent_package.save()

    logger.info(
        "DRPP committed: %s -> %s/%d/%s",
        preview_state.nomor_drpp,
        parent_package.satker_code,
        parent_package.tahun,
        parent_package.nomor_spm,
    )

    return True, "DRPP berhasil disimpan.", parent_package


def check_package_duplicate(
    satker_code: str,
    tahun: int,
    nomor_spm: str,
) -> list[TransactionPackage]:
    """
    Check for potential duplicate packages.

    Returns list of packages with the same identity.
    """
    satker_code = normalize_satker(satker_code)
    nomor_spm = normalize_nomor_spm(nomor_spm)
    tahun = normalize_tahun(tahun)

    return list(TransactionPackage.objects.filter(
        satker_code=satker_code,
        tahun=tahun,
        nomor_spm=nomor_spm,
    ))


def get_package_by_identity(
    satker_code: str,
    tahun: int,
    nomor_spm: str,
) -> Optional[TransactionPackage]:
    """Get a package by its canonical identity."""
    satker_code = normalize_satker(satker_code)
    nomor_spm = normalize_nomor_spm(nomor_spm)
    tahun = normalize_tahun(tahun)

    return TransactionPackage.objects.filter(
        satker_code=satker_code,
        tahun=tahun,
        nomor_spm=nomor_spm,
    ).first()
