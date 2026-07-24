from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, F, Q
from django.shortcuts import render

from apps.accounts.access import can_edit_satker, filter_by_satker, get_profile, permission_context
from apps.core.views import D_K_COLUMNS, MONTH_OPTIONS, build_satker_options, get_satker_name_map
from apps.documents.models import ChecklistStatus
from apps.paket_spm.models import PaketSPMPreviewItem
from apps.sp2d.models import SP2DRaw

from .models import MasterAkun, TransactionDetail
from .services import has_drpp_record, requires_drpp


PAGE_SIZE_OPTIONS = (20, 50, 100)


@login_required
def transaction_list(request):
    base_queryset = TransactionDetail.objects.select_related("sp2d_raw", "created_by").prefetch_related("checklist_statuses")
    global_total = TransactionDetail.objects.count()
    scoped_queryset = filter_by_satker(base_queryset, request.user)
    scoped_total = scoped_queryset.count()
    queryset = scoped_queryset
    queryset = queryset.annotate(
        checklist_total=Count("checklist_statuses", distinct=True),
        checklist_ada=Count(
            "checklist_statuses",
            filter=Q(checklist_statuses__status=ChecklistStatus.Status.ADA),
            distinct=True,
        ),
    )

    filters = {
        "q": request.GET.get("q", "").strip(),
        "satker": request.GET.get("satker", "").strip(),
        "bulan": request.GET.get("bulan", "").strip(),
        "akun": request.GET.get("akun", "").strip(),
        "kelengkapan": request.GET.get("kelengkapan", "").strip(),
        "page_size": request.GET.get("page_size", "").strip(),
    }
    if filters["q"]:
        search = filters["q"]
        matching_satker_codes = list(
            SP2DRaw.objects.filter(satker_name__icontains=search)
            .exclude(satker_code="")
            .values_list("satker_code", flat=True)
            .distinct()
        )
        queryset = queryset.filter(
            Q(nomor_spm__icontains=search)
            | Q(no_kuitansi__icontains=search)
            | Q(no_drpp__icontains=search)
            | Q(deskripsi__icontains=search)
            | Q(akun__icontains=search)
            | Q(pembebanan__icontains=search)
            | Q(satker_code__icontains=search)
            | Q(sp2d_raw__satker_name__icontains=search)
            | Q(satker_code__in=matching_satker_codes)
        )
    if filters["satker"]:
        queryset = queryset.filter(satker_code=filters["satker"])
    if filters["bulan"]:
        queryset = queryset.filter(bulan_sp2d=filters["bulan"])
    if filters["akun"]:
        account_filter = filters["akun"]
        if account_filter.lower().endswith("x"):
            account_filter = account_filter.rstrip("xX")
            queryset = queryset.filter(akun__startswith=account_filter)
        elif len(account_filter) <= 2:
            queryset = queryset.filter(akun__startswith=account_filter)
        else:
            queryset = queryset.filter(akun=account_filter)
    if filters["kelengkapan"] == "lengkap":
        queryset = queryset.filter(checklist_total__gt=0, checklist_ada__gte=F("checklist_total"))
    elif filters["kelengkapan"] == "belum":
        queryset = queryset.exclude(checklist_total__gt=0, checklist_ada__gte=F("checklist_total"))

    filtered_total = queryset.count()
    page_size = normalize_page_size(filters["page_size"])
    filters["page_size"] = str(page_size)
    queryset = queryset.order_by("satker_code", "bulan_sp2d", "nomor_spm", "id")
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = list(page_obj.object_list)
    attach_satker_names(rows)
    attach_source_labels(rows)
    for row in rows:
        row.can_edit = can_edit_satker(request.user, row.satker_code)
        row.requires_drpp = requires_drpp(row)
        row.has_drpp_record = has_drpp_record(row)

    page_query = request.GET.copy()
    page_query.pop("page", None)
    base_querystring = page_query.urlencode()

    profile = get_profile(request.user)
    context = permission_context(request.user)
    context.update(
        {
            "page_title": "Database D_K",
            "page_subtitle": "Detail transaksi keuangan lengkap; dari sini buka Checklist dan DRPP.",
            "columns": D_K_COLUMNS,
            "rows": rows,
            "filters": filters,
            "satker_options": get_satker_options(request.user),
            "akun_options": MasterAkun.objects.filter(is_active=True).order_by("kode")[:200],
            "months": MONTH_OPTIONS,
            "page_obj": page_obj,
            "paginator": paginator,
            "page_size": page_size,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "page_start": page_obj.start_index() if paginator.count else 0,
            "page_end": page_obj.end_index() if paginator.count else 0,
            "base_querystring": base_querystring,
            "pagination_window": build_pagination_window(page_obj),
            "dk_diagnostic": {
                "db_backend": connection.vendor,
                "db_name": connection.settings_dict.get("NAME"),
                "username": request.user.get_username(),
                "role": getattr(profile, "role", "-") if profile else "-",
                "satker_scope": getattr(profile, "satker_code", "") if profile else "",
                "global_total": global_total,
                "scoped_total": scoped_total,
                "filtered_total": filtered_total,
                "page_size": page_size,
                "num_pages": paginator.num_pages,
                "current_page": page_obj.number,
            },
        }
    )
    return render(request, "dk/list.html", context)


def normalize_page_size(value):
    try:
        page_size = int(value)
    except (TypeError, ValueError):
        return 20
    return page_size if page_size in PAGE_SIZE_OPTIONS else 20


def build_pagination_window(page_obj):
    number = page_obj.number
    total = page_obj.paginator.num_pages
    pages = {1, total, number - 1, number, number + 1}
    if number <= 3:
        pages.update(range(1, min(total, 4) + 1))
    if number >= total - 2:
        pages.update(range(max(1, total - 3), total + 1))
    return [page for page in sorted(pages) if 1 <= page <= total]


def attach_satker_names(rows):
    codes = {row.satker_code for row in rows if row.satker_code}
    names = get_satker_name_map(codes)
    for row in rows:
        row.display_satker_name = names.get(row.satker_code, "")


def attach_source_labels(rows):
    row_ids = [row.id for row in rows]
    package_items = {
        item.matched_transaction_id: item
        for item in PaketSPMPreviewItem.objects.filter(matched_transaction_id__in=row_ids).select_related("paket").order_by("-created_at")
    }
    for row in rows:
        package_item = package_items.get(row.id)
        if package_item:
            row.source_data_label = "Paket SPM"
            row.document_status_label = row.get_status_detail_display()
            row.reconciliation_status_label = extract_note_value(package_item.catatan, "Status Rekonsiliasi") or "Belum ada SP2D pembanding"
            row.display_no_sp2d = row.sp2d_raw.no_sp2d if row.sp2d_raw_id and row.sp2d_raw else "Belum ada SP2D pembanding"
        elif row.sp2d_raw_id:
            row.source_data_label = "SP2D Excel"
            row.document_status_label = "Belum Lengkap"
            row.reconciliation_status_label = "Data awal dari SP2D"
            row.display_no_sp2d = row.sp2d_raw.no_sp2d if row.sp2d_raw else "-"
        else:
            row.source_data_label = "D_K"
            row.document_status_label = row.get_status_detail_display()
            row.reconciliation_status_label = "Belum ada SP2D pembanding"
            row.display_no_sp2d = "Belum ada SP2D pembanding"


def extract_note_value(text, key):
    prefix = f"{key}="
    for part in str(text or "").split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix):].strip()
    return ""


def get_satker_options(user):
    queryset = filter_by_satker(TransactionDetail.objects.exclude(satker_code=""), user)
    return build_satker_options(queryset)

from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from .forms import TransactionDetailForm
from .models import TransactionChangeLog

@login_required
def transaction_create(request):
    if request.method == "POST":
        form = TransactionDetailForm(request.POST)
        if form.is_valid():
            satker_code = form.cleaned_data.get('satker_code')
            if not can_edit_satker(request.user, satker_code):
                raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
            instance = form.save(commit=False)
            instance.created_by = request.user
            instance.save()
            messages.success(request, "Baris D_K berhasil ditambahkan.")
            return redirect('dk:transaction_list')
    else:
        form = TransactionDetailForm()
    
    context = permission_context(request.user)
    context.update({'form': form, 'page_title': 'Tambah Baris D_K'})
    return render(request, 'dk/form.html', context)

def log_transaction_changes(instance, form, user, source="MANUAL"):
    if not form.has_changed():
        return
    for field in form.changed_data:
        old_val = form.initial.get(field)
        new_val = form.cleaned_data.get(field)
        TransactionChangeLog.objects.create(
            transaction=instance,
            field_name=field,
            old_value=str(old_val) if old_val is not None else "",
            new_value=str(new_val) if new_val is not None else "",
            change_source=source,
            changed_by=user
        )

@login_required
def transaction_edit(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if request.method == "POST":
        form = TransactionDetailForm(request.POST, instance=instance)
        if form.is_valid():
            satker_code = form.cleaned_data.get('satker_code')
            if not can_edit_satker(request.user, satker_code):
                raise PermissionDenied("Anda tidak memiliki izin memindahkan ke satker ini.")
            instance = form.save()
            log_transaction_changes(instance, form, request.user, source="MANUAL")
            messages.success(request, "Baris D_K berhasil diubah.")
            return redirect('dk:transaction_list')
    else:
        form = TransactionDetailForm(instance=instance)
    
    context = permission_context(request.user)
    context.update({'form': form, 'page_title': 'Edit Baris D_K', 'instance': instance})
    return render(request, 'dk/form.html', context)

@login_required
def transaction_duplicate(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if request.method == "POST":
        instance.pk = None
        instance.status_detail = TransactionDetail.StatusDetail.DRAFT
        instance.created_by = request.user
        instance.save()
        messages.success(request, "Baris D_K berhasil diduplikat.")
        return redirect('dk:transaction_edit', pk=instance.pk)
    return redirect('dk:transaction_list')

@login_required
def transaction_archive(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if request.method == "POST":
        instance.status_detail = TransactionDetail.StatusDetail.DIARSIPKAN
        instance.save()
        TransactionChangeLog.objects.create(transaction=instance, field_name="status_detail", old_value="", new_value="DIARSIPKAN", change_source="MANUAL", changed_by=request.user)
        messages.success(request, "Baris D_K berhasil diarsipkan.")
    return redirect('dk:transaction_list')

