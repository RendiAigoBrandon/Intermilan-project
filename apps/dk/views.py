from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, F, Q
from django.shortcuts import render

from apps.accounts.access import can_edit_satker, filter_by_satker, get_profile, permission_context, is_viewer
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
        "archive_status": request.GET.get("archive_status", "aktif").strip(),
    }
    
    if filters["archive_status"] == "arsip":
        queryset = queryset.filter(status_detail=TransactionDetail.StatusDetail.DIARSIPKAN)
    elif filters["archive_status"] == "semua":
        pass
    else:
        queryset = queryset.exclude(status_detail=TransactionDetail.StatusDetail.DIARSIPKAN)

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
    
    # Query SP2D Headers for the active scope
    sp2d_qs = filter_by_satker(SP2DRaw.objects.select_related("import_batch"), request.user)
    
    sp2d_status_filter = request.GET.get("sp2d_status", "")
    if sp2d_status_filter == "semua":
        pass
    else:
        # Default: only PERLU_DETAIL, TIDAK_COCOK, DRAFT
        sp2d_qs = sp2d_qs.filter(status__in=[SP2DRaw.Status.PERLU_DETAIL, SP2DRaw.Status.TIDAK_COCOK, SP2DRaw.Status.DRAFT])
        
    if filters["satker"]:
        sp2d_qs = sp2d_qs.filter(satker_code=filters["satker"])
    if filters["bulan"]:
        sp2d_qs = sp2d_qs.filter(bulan_sp2d=filters["bulan"])
    if filters["q"]:
        # Only simple filter for SP2D matching D_K query
        search = filters["q"]
        sp2d_qs = sp2d_qs.filter(
            Q(no_sp2d__icontains=search)
            | Q(nomor_invoice__icontains=search)
            | Q(nomor_spm_extracted__icontains=search)
            | Q(deskripsi__icontains=search)
            | Q(satker_code__icontains=search)
        )
        
    sp2d_qs = sp2d_qs.order_by("-created_at")
    
    # Separate pagination for SP2D headers (for simplicity, using a different page param or just first 20)
    # The requirement says "Header SP2D pada D_K harus dipaginasi"
    # Let's use `sp2d_page` param
    sp2d_page_size = 5
    sp2d_paginator = Paginator(sp2d_qs, sp2d_page_size)
    sp2d_page_obj = sp2d_paginator.get_page(request.GET.get("sp2d_page"))
    
    sp2d_page_query = request.GET.copy()
    sp2d_page_query.pop("sp2d_page", None)
    sp2d_base_querystring = sp2d_page_query.urlencode()

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
            "sp2d_page_obj": sp2d_page_obj,
            "sp2d_paginator": sp2d_paginator,
            "sp2d_base_querystring": sp2d_base_querystring,
            "sp2d_start": sp2d_page_obj.start_index() if sp2d_paginator.count else 0,
            "sp2d_end": sp2d_page_obj.end_index() if sp2d_paginator.count else 0,
            "sp2d_status_filter": sp2d_status_filter,
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
from django.db import transaction
from .forms import TransactionDetailForm, TransactionBulkEditForm
from .models import TransactionChangeLog

@login_required
@transaction.atomic
def transaction_create(request):
    if is_viewer(request.user):
        raise PermissionDenied("Anda tidak memiliki izin (Read-Only).")
    if request.method == "POST":
        form = TransactionDetailForm(request.POST, user=request.user)
        if form.is_valid():
            satker_code = form.cleaned_data.get('satker_code')
            if not can_edit_satker(request.user, satker_code):
                raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
            instance = form.save(commit=False)
            instance.created_by = request.user
            
            # SP2D Linkage only on create
            sp2d_raw_id = form.cleaned_data.get("sp2d_raw_id")
            linked_sp2d = None
            if sp2d_raw_id:
                sp2d = filter_by_satker(SP2DRaw.objects, request.user).filter(id=sp2d_raw_id).first()
                if sp2d and sp2d.satker_code == instance.satker_code and can_edit_satker(request.user, sp2d.satker_code):
                    instance.sp2d_raw = sp2d
                    instance.status_detail = TransactionDetail.StatusDetail.DRAFT
                    linked_sp2d = sp2d
                else:
                    form.add_error(None, "SP2D tidak ditemukan atau beda satker.")
                    context = permission_context(request.user)
                    context.update({'form': form, 'page_title': 'Tambah Baris D_K'})
                    return render(request, 'dk/form.html', context)

            instance.save()
            
            # Log fields that were filled
            for field in form.cleaned_data:
                new_val = form.cleaned_data.get(field)
                if new_val is not None and new_val != "":
                    TransactionChangeLog.objects.create(
                        transaction=instance,
                        field_name=field,
                        old_value="",
                        new_value=str(new_val),
                        change_source=TransactionChangeLog.ChangeSource.MANUAL,
                        changed_by=request.user
                    )
            
            if linked_sp2d:
                TransactionChangeLog.objects.create(
                    transaction=instance,
                    field_name="sp2d_raw",
                    old_value="",
                    new_value=str(linked_sp2d.id),
                    change_source=TransactionChangeLog.ChangeSource.MANUAL,
                    changed_by=request.user
                )
                from apps.sp2d.services import reconcile_sp2d_with_dk
                reconcile_sp2d_with_dk(linked_sp2d, request.user)
                    
            messages.success(request, "Baris D_K berhasil ditambahkan.")
            if "save_and_add" in request.POST:
                return redirect('dk:transaction_create')
            return redirect('dk:transaction_list')
    else:
        initial_data = {}
        sp2d_id = request.GET.get("sp2d_raw_id")
        if sp2d_id:
            sp2d = filter_by_satker(SP2DRaw.objects, request.user).filter(id=sp2d_id).first()
            if sp2d:
                initial_data = {
                    "sp2d_raw_id": sp2d.id,
                    "satker_code": sp2d.satker_code,
                    "nomor_spm": sp2d.nomor_spm_extracted,
                    "tanggal_spm": sp2d.tanggal_invoice or sp2d.tgl_sp2d or sp2d.tanggal_selesai_sp2d,
                    "bulan_sp2d": sp2d.bulan_sp2d,
                    "cara_pembayaran": sp2d.jenis_spm,
                    "jenis_spm": sp2d.jenis_spm,
                    "deskripsi": sp2d.deskripsi,
                }
        form = TransactionDetailForm(user=request.user, initial=initial_data)
    
    context = permission_context(request.user)
    context.update({'form': form, 'page_title': 'Tambah Baris D_K'})
    return render(request, 'dk/form.html', context)

def log_transaction_changes(instance, form, user, source=TransactionChangeLog.ChangeSource.MANUAL):
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
@transaction.atomic
def transaction_edit(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if request.method == "POST":
        form = TransactionDetailForm(request.POST, instance=instance, user=request.user)
        if form.is_valid():
            satker_code = form.cleaned_data.get('satker_code')
            if not can_edit_satker(request.user, satker_code):
                raise PermissionDenied("Anda tidak memiliki izin memindahkan ke satker ini.")
            instance = form.save()
            log_transaction_changes(instance, form, request.user, source=TransactionChangeLog.ChangeSource.MANUAL)
            messages.success(request, "Baris D_K berhasil diubah.")
            return redirect('dk:transaction_list')
    else:
        form = TransactionDetailForm(instance=instance, user=request.user)
    
    context = permission_context(request.user)
    context.update({'form': form, 'page_title': 'Edit Baris D_K', 'instance': instance})
    return render(request, 'dk/form.html', context)

@login_required
@transaction.atomic
def transaction_duplicate(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if request.method == "POST":
        original_pk = instance.pk
        instance.pk = None
        instance.status_detail = TransactionDetail.StatusDetail.DRAFT
        instance.created_by = request.user
        instance.save()
        
        # Log the duplication source on the NEW instance
        TransactionChangeLog.objects.create(
            transaction=instance,
            field_name="duplicated_from",
            old_value=str(original_pk),
            new_value=str(instance.pk),
            change_source=TransactionChangeLog.ChangeSource.MANUAL,
            changed_by=request.user
        )
        
        messages.success(request, "Baris D_K berhasil diduplikat.")
        return redirect('dk:transaction_edit', pk=instance.pk)
    return redirect('dk:transaction_list')

@login_required
@transaction.atomic
def transaction_archive(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if instance.status_detail == TransactionDetail.StatusDetail.DIARSIPKAN:
        messages.error(request, "Baris D_K sudah dalam status DIARSIPKAN.")
        return redirect('dk:transaction_list')
    if request.method == "POST":
        old_status = instance.status_detail
        instance.status_detail = TransactionDetail.StatusDetail.DIARSIPKAN
        instance.save(update_fields=['status_detail'])
        TransactionChangeLog.objects.create(
            transaction=instance,
            field_name="status_detail",
            old_value=str(old_status),
            new_value="DIARSIPKAN",
            change_source=TransactionChangeLog.ChangeSource.MANUAL,
            changed_by=request.user
        )
        messages.success(request, "Baris D_K berhasil diarsipkan.")
    return redirect('dk:transaction_list')

@login_required
@transaction.atomic
def transaction_restore(request, pk):
    instance = get_object_or_404(TransactionDetail, pk=pk)
    if not can_edit_satker(request.user, instance.satker_code):
        raise PermissionDenied("Anda tidak memiliki izin pada satker ini.")
    if instance.status_detail != TransactionDetail.StatusDetail.DIARSIPKAN:
        messages.error(request, "Baris D_K tidak dalam status DIARSIPKAN.")
        return redirect('dk:transaction_list')
    if request.method == "POST":
        # Find the last archive log to get original status
        last_log = instance.change_logs.filter(
            field_name="status_detail", 
            new_value="DIARSIPKAN"
        ).order_by("-changed_at").first()
        
        original_status = last_log.old_value if last_log and last_log.old_value else TransactionDetail.StatusDetail.DRAFT
        
        valid_statuses = [c.value for c in TransactionDetail.StatusDetail]
        if original_status not in valid_statuses or original_status == TransactionDetail.StatusDetail.DIARSIPKAN:
            original_status = TransactionDetail.StatusDetail.DRAFT
            
        instance.status_detail = original_status
        instance.save(update_fields=['status_detail'])
        TransactionChangeLog.objects.create(
            transaction=instance,
            field_name="status_detail",
            old_value="DIARSIPKAN",
            new_value=str(original_status),
            change_source=TransactionChangeLog.ChangeSource.MANUAL,
            changed_by=request.user
        )
        messages.success(request, "Baris D_K berhasil dipulihkan.")
    return redirect('dk:transaction_list')

@login_required
@transaction.atomic
def transaction_bulk_edit(request):
    if is_viewer(request.user):
        raise PermissionDenied("Anda tidak memiliki izin (Read-Only).")
        
    if request.method == "POST":
        selected_ids = request.POST.getlist('selected_ids')
    else:
        selected_ids = request.GET.getlist('ids')
        
    try:
        selected_ids = [int(i) for i in selected_ids if str(i).strip()]
    except ValueError:
        messages.error(request, "ID tidak valid.")
        return redirect('dk:transaction_list')

    if not selected_ids:
        messages.warning(request, "Pilih baris terlebih dahulu.")
        return redirect('dk:transaction_list')
        
    transactions = TransactionDetail.objects.filter(id__in=selected_ids)
    if transactions.count() != len(set(selected_ids)):
        messages.error(request, "Satu atau lebih baris data tidak ditemukan.")
        return redirect('dk:transaction_list')
        
    for t in transactions:
        if not can_edit_satker(request.user, t.satker_code):
            messages.error(request, f"Anda tidak memiliki izin untuk mengedit baris dengan satker {t.satker_code}.")
            return redirect('dk:transaction_list')
        if t.status_detail == TransactionDetail.StatusDetail.DIARSIPKAN:
            messages.error(request, "Tidak dapat melakukan bulk edit pada baris yang diarsipkan.")
            return redirect('dk:transaction_list')
            
    if request.method == "POST":
        action = request.POST.get('action')
        form = TransactionBulkEditForm(request.POST)
        if form.is_valid():
            if action == 'preview':
                preview_data = {
                    'count': transactions.count(),
                    'changes': []
                }
                for field in ['bulan_sp2d', 'cara_pembayaran', 'jenis_spm', 'status_detail']:
                    new_val = form.cleaned_data.get(field)
                    if new_val:
                        preview_data['changes'].append({'field': field, 'new_value': new_val})
                
                context = permission_context(request.user)
                context.update({
                    'form': form, 
                    'page_title': 'Preview Bulk Edit D_K', 
                    'selected_ids': selected_ids,
                    'preview_data': preview_data
                })
                return render(request, 'dk/bulk_edit.html', context)
            elif action == 'commit':
                updated_count = 0
                for t in transactions:
                    changed = False
                    for field in ['bulan_sp2d', 'cara_pembayaran', 'jenis_spm', 'status_detail']:
                        new_val = form.cleaned_data.get(field)
                        if new_val:
                            old_val = getattr(t, field)
                            if str(old_val) != str(new_val):
                                setattr(t, field, new_val)
                                changed = True
                                TransactionChangeLog.objects.create(
                                    transaction=t,
                                    field_name=field,
                                    old_value=str(old_val) if old_val is not None else "",
                                    new_value=str(new_val),
                                    change_source=TransactionChangeLog.ChangeSource.MANUAL,
                                    changed_by=request.user
                                )
                    if changed:
                        t.save()
                        updated_count += 1
                        
                messages.success(request, f"{updated_count} baris D_K berhasil diubah secara bulk.")
                return redirect('dk:transaction_list')
    else:
        form = TransactionBulkEditForm()
        
    context = permission_context(request.user)
    context.update({'form': form, 'page_title': 'Bulk Edit D_K', 'selected_ids': selected_ids})
    return render(request, 'dk/bulk_edit.html', context)

