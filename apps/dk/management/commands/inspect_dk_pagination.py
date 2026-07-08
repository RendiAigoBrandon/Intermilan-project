from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q

from apps.accounts.access import filter_by_satker, get_profile
from apps.documents.models import ChecklistStatus
from apps.dk.views import normalize_page_size
from apps.sp2d.models import SP2DRaw

from ...models import TransactionDetail


class Command(BaseCommand):
    help = "Audit read-only queryset dan pagination halaman D_K."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="Username untuk simulasi permission scope.")
        parser.add_argument("--q", default="", help="Search D_K.")
        parser.add_argument("--satker", default="", help="Filter satker.")
        parser.add_argument("--bulan", default="", help="Filter bulan 1-12.")
        parser.add_argument("--akun", default="", help="Filter akun.")
        parser.add_argument("--page", default="1", help="Nomor halaman.")
        parser.add_argument("--page-size", default="20", help="Page size: 20, 50, atau 100.")

    def handle(self, *args, **options):
        user = self.get_user(options["username"])
        queryset = TransactionDetail.objects.select_related("sp2d_raw", "created_by")
        scoped_queryset = filter_by_satker(queryset, user) if user else queryset
        filtered_queryset = scoped_queryset.annotate(
            checklist_total=Count("checklist_statuses", distinct=True),
            checklist_ada=Count(
                "checklist_statuses",
                filter=Q(checklist_statuses__status=ChecklistStatus.Status.ADA),
                distinct=True,
            ),
        )

        if options["q"]:
            search = options["q"]
            matching_satker_codes = list(
                SP2DRaw.objects.filter(satker_name__icontains=search)
                .exclude(satker_code="")
                .values_list("satker_code", flat=True)
                .distinct()
            )
            filtered_queryset = filtered_queryset.filter(
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
        if options["satker"]:
            filtered_queryset = filtered_queryset.filter(satker_code=options["satker"])
        if options["bulan"]:
            filtered_queryset = filtered_queryset.filter(bulan_sp2d=options["bulan"])
        if options["akun"]:
            filtered_queryset = filtered_queryset.filter(akun=options["akun"])

        page_size = normalize_page_size(options["page_size"])
        ordered_queryset = filtered_queryset.order_by("satker_code", "bulan_sp2d", "nomor_spm", "id")
        paginator = Paginator(ordered_queryset, page_size)
        page_obj = paginator.get_page(options["page"])
        profile = get_profile(user) if user else None

        self.stdout.write("Audit D_K pagination (read-only)")
        self.stdout.write(f"- DB backend: {connection.vendor}")
        self.stdout.write(f"- DB name: {connection.settings_dict.get('NAME')}")
        self.stdout.write(f"- User login: {user.get_username() if user else '(tanpa user/scope admin-like)'}")
        self.stdout.write(f"- Role: {getattr(profile, 'role', '-') if profile else '-'}")
        self.stdout.write(f"- Satker scope: {getattr(profile, 'satker_code', '') if profile else '-'}")
        self.stdout.write(f"- Total TransactionDetail global: {TransactionDetail.objects.count()}")
        self.stdout.write(f"- Total setelah permission scope: {scoped_queryset.count()}")
        self.stdout.write(f"- Total setelah filter/search: {filtered_queryset.count()}")
        self.stdout.write(f"- Page size: {page_size}")
        self.stdout.write(f"- Jumlah page: {paginator.num_pages}")
        self.stdout.write(f"- Current page: {page_obj.number}")
        self.stdout.write(f"- Row di page aktif: {len(page_obj.object_list)}")

    def get_user(self, username):
        if not username:
            return None
        return get_user_model().objects.filter(username=username).first()
