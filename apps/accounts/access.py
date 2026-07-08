from django.core.exceptions import PermissionDenied

from .models import Profile


def get_profile(user):
    if not user.is_authenticated:
        return None
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


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


def get_user_satker_code(user):
    profile = get_profile(user)
    return profile.satker_code if profile else ""


def can_view_all_satker(user):
    return is_admin(user) or is_viewer(user)


def can_view_transaction(user, transaction):
    if is_admin(user) or is_viewer(user):
        return True
    profile = get_profile(user)
    if profile and profile.is_satker:
        return profile.satker_code == getattr(transaction, "satker_code", "")
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


def can_export_data(user):
    return is_admin(user) or is_operator_satker(user)


def filter_by_satker(queryset, user, field_name="satker_code"):
    profile = get_profile(user)
    if not profile or is_admin(user):
        return queryset
    if profile.is_satker:
        return queryset.filter(**{field_name: profile.satker_code})
    return queryset


def require_write_access(user):
    if is_viewer(user):
        raise PermissionDenied("Viewer hanya memiliki akses baca.")


def can_edit_satker(user, satker_code):
    if is_admin(user):
        return True
    profile = get_profile(user)
    if not profile:
        return False
    if profile.is_satker:
        return profile.satker_code == satker_code
    return False


def permission_context(user):
    return {
        "is_role_admin": is_admin(user),
        "is_role_operator": is_operator_satker(user),
        "is_role_viewer": is_viewer(user),
        "user_satker_code": get_user_satker_code(user),
        "can_view_all_satker": can_view_all_satker(user),
        "can_upload_document": can_upload_document(user),
        "can_access_audit_data": can_access_audit_data(user),
        "can_import_data": can_import_data(user),
        "can_export_data": can_export_data(user),
    }
