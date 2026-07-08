from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import MonitoringSummary
from apps.dk.models import TransactionDetail
from apps.documents.models import ChecklistStatus, DocumentDriveLink


def refresh_monitoring_summary(tahun=None, bulan=None, satker_code=None):
    queryset = MonitoringSummary.objects.all()
    if tahun:
        queryset = queryset.filter(tahun=tahun)
    if bulan:
        queryset = queryset.filter(bulan_number=bulan)
    if satker_code:
        queryset = queryset.filter(satker_code=satker_code)

    refreshed = 0
    for summary in queryset:
        refresh_monitoring_summary_row(summary)
        refreshed += 1
    return refreshed


def refresh_monitoring_summary_row(summary: MonitoringSummary):
    current_transactions = TransactionDetail.objects.filter(
        satker_code=summary.satker_code,
        bulan_sp2d=summary.bulan_number,
    )
    cumulative_transactions = TransactionDetail.objects.filter(
        satker_code=summary.satker_code,
        bulan_sp2d__lte=summary.bulan_number,
    )
    current_total = current_transactions.aggregate(total=Sum("nilai_netto"))["total"] or Decimal("0")
    cumulative_total = cumulative_transactions.aggregate(total=Sum("nilai_netto"))["total"] or Decimal("0")

    summary.intermilan_bulan_ini = current_total
    summary.intermilan_sd_bulan_ini = cumulative_total
    summary.persen_realisasi = calculate_realisasi_percent(current_total, summary.fa16_bulan_ini)
    update_document_percentages(summary, current_transactions)
    summary.last_refreshed_at = timezone.now()
    summary.source = MonitoringSummary.Source.MIXED if summary.fa16_bulan_ini else MonitoringSummary.Source.CALCULATED
    summary.save(
        update_fields=[
            "intermilan_bulan_ini",
            "intermilan_sd_bulan_ini",
            "persen_realisasi",
            "persen_kelengkapan_dokumen",
            "persen_spj_upload",
            "last_refreshed_at",
            "source",
            "updated_at",
        ]
    )
    return summary


def calculate_realisasi_percent(intermilan_value, fa16_value):
    intermilan_value = Decimal(intermilan_value or 0)
    fa16_value = Decimal(fa16_value or 0)
    if fa16_value <= 0:
        return Decimal("0")
    return min((intermilan_value / fa16_value) * Decimal("100"), Decimal("100")).quantize(Decimal("0.01"))


def update_document_percentages(summary: MonitoringSummary, transactions):
    transaction_ids = list(transactions.values_list("id", flat=True))
    if not transaction_ids:
        return

    checklist_total = ChecklistStatus.objects.filter(transaction_detail_id__in=transaction_ids).count()
    if checklist_total:
        checklist_ada = ChecklistStatus.objects.filter(
            transaction_detail_id__in=transaction_ids,
            status=ChecklistStatus.Status.ADA,
        ).count()
        summary.persen_kelengkapan_dokumen = percent(checklist_ada, checklist_total)

    linked_transactions = (
        DocumentDriveLink.objects.filter(transaction_detail_id__in=transaction_ids)
        .exclude(transaction_detail_id__isnull=True)
        .values("transaction_detail_id")
        .distinct()
        .count()
    )
    if linked_transactions:
        summary.persen_spj_upload = percent(linked_transactions, len(transaction_ids))


def percent(numerator, denominator):
    if not denominator:
        return Decimal("0")
    return min((Decimal(numerator) / Decimal(denominator)) * Decimal("100"), Decimal("100")).quantize(Decimal("0.01"))
