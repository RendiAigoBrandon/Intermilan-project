from django.core.exceptions import PermissionDenied
import logging

from .models import Profile

logger = logging.getLogger(__name__)


def get_profile(user):
    if not user.is_authenticated:
        return None
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def get_user_satker_code(user):
    """Get raw 4-digit satker_code dari profile (untuk display."""
    profile = get_profile(user)
    return profile.satker_code if profile else ""


def get_user_official_satker_code(user):
    """
    Ambil 6-digit official satker_code dari profile user.

    Konversi dari 4-digit unit_code jika perlu via SatkerMaster mapping.

    Returns:
        6-digit satker_code jika sukses mapping
        "" (empty) jika gagal mapping (dengan WARNING log)

    CATATAN: Tidak ada silent fallback - jika mapping gagal, log WARNING dan return empty.
    """
    profile = get_profile(user)
    if not profile or not profile.satker_code:
        logger.warning(
            f"User {getattr(user, 'username', 'unknown')}: profile atau satker_code kosong"
        )
        return ""

    # Jika sudah 6-digit (format official BPS), return langsung
    if len(profile.satker_code) == 6:
        return profile.satker_code

    # Convert 4-digit ke 6-digit via SatkerMaster
    from apps.core.models import SatkerMaster
    try:
        satker = SatkerMaster.objects.get(unit_code=profile.satker_code)
        official_code = satker.satker_code
        logger.debug(
            f"User {getattr(user, 'username', 'unknown')}: "
            f"Converted {profile.satker_code} -> {official_code}"
        )
        return official_code
    except SatkerMaster.DoesNotExist:
        logger.warning(
            f"User {getattr(user, 'username', 'unknown')}: "
            f"FAILED mapping {profile.satker_code} - SatkerMaster not found. Akses DITOLAK."
        )
        return ""  # Gagal mapping - return empty (access denied)


def is_admin(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = get_profile(user)
    return bool(profile and profile.is_admin_pusat)


def is_admin_pusat(user):
    return is_admin(user)


def is_operator_satker(user):
    profile = get_profile(user)
    return bool(profile and profile.is_satker)


def is_viewer(user):
    profile = get_profile(user)
    return bool(profile and profile.is_viewer)


def get_user_scope_label(user):
    profile = get_profile(user)
    if not profile:
        return ""
    if is_admin(user):
        return "Semua Satker"
    if profile.is_satker:
        label = f"Satker {profile.satker_code}" if profile.satker_code else "Satker"
        return f"{label} - {profile.satker_name}" if profile.satker_name else label
    if profile.is_viewer:
        return "Semua Satker (Read Only)"
    return ""


def can_view_all_satker(user):
    return is_admin(user) or is_viewer(user)


def can_view_transaction(user, transaction):
    if is_admin(user) or is_viewer(user):
        return True
    profile = get_profile(user)
    if profile and profile.is_satker:
        official_code = get_user_official_satker_code(user)
        return official_code == getattr(transaction, "satker_code", "")
    return False


def can_edit_transaction(user, transaction):
    return can_edit_satker(user, getattr(transaction, "satker_code", ""))


def can_upload_document(user, transaction=None):
    if is_admin(user):
        return True
    if is_viewer(user):
        return False
    if transaction is None:
        return is_operator_satker(user)
    return can_edit_transaction(user, transaction)


def can_access_audit_data(user):
    return is_admin(user)


def can_import_data(user):
    return is_admin(user)


def can_upload_sp2d(user):
    """
    Permission upload SP2D:
    - Admin: boleh upload semua satker
    - Satker: boleh upload satkernya sendiri

    Note: satker_code diambil dari profile user, bukan dari Excel.
    """
    if is_admin(user):
        return True
    if is_operator_satker(user):
        return True
    return False


def get_satker_from_code(satker_code):
    """
    Ambil data satker dari satker_code (6-digit) atau unit_code (4-digit).
    Returns dict dengan unit_code dan nama_satker.

    Lookup order:
    1. SatkerMaster.satker_code (6-digit official)
    2. SatkerMaster.unit_code (4-digit legacy)
    """
    from apps.core.models import SatkerMaster
    try:
        satker = SatkerMaster.objects.get(satker_code=satker_code)
        return {
            "unit_code": satker.unit_code,
            "nama_satker": satker.nama_satker,
        }
    except SatkerMaster.DoesNotExist:
        pass

    # Try lookup by unit_code (4-digit)
    try:
        satker = SatkerMaster.objects.get(unit_code=satker_code)
        return {
            "unit_code": satker.unit_code,
            "nama_satker": satker.nama_satker,
        }
    except SatkerMaster.DoesNotExist:
        return None


def can_export_data(user):
    return is_admin(user) or is_operator_satker(user)


def filter_by_satker(queryset, user, field_name="satker_code"):
    """Filter queryset berdasarkan satker user login."""
    profile = get_profile(user)
    if not profile or is_admin(user):
        return queryset
    if profile.is_satker:
        # Gunakan official 6-digit satker_code
        official_code = get_user_official_satker_code(user)
        if not official_code:
            # Mapping gagal - return empty queryset (tidak ada akses)
            return queryset.none()
        return queryset.filter(**{field_name: official_code})
    return queryset


def require_write_access(user):
    if is_viewer(user):
        raise PermissionDenied("Viewer hanya memiliki akses baca.")


def can_edit_satker(user, satker_code):
    """Cek apakah user boleh edit data satker tertentu."""
    if is_admin(user):
        return True
    profile = get_profile(user)
    if not profile:
        return False
    if profile.is_satker:
        official_code = get_user_official_satker_code(user)
        if not official_code:
            return False  # Mapping gagal - ditolak
        return official_code == satker_code
    return False


def permission_context(user):
    return {
        "is_role_admin": is_admin(user),
        "is_role_operator": is_operator_satker(user),
        "is_role_viewer": is_viewer(user),
        "user_satker_code": get_user_satker_code(user),
        "user_scope": get_user_scope_label(user),
        "can_view_all_satker": can_view_all_satker(user),
        "can_upload_document": can_upload_document(user),
        "can_access_audit_data": can_access_audit_data(user),
        "can_import_data": can_import_data(user),
        "can_upload_sp2d": can_upload_sp2d(user),
        "can_export_data": can_export_data(user),
    }
