from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.accounts.access import permission_context
from apps.dk.models import MasterAkun, TransactionDetail

@login_required
def report_home(request):
    context = permission_context(request.user)
    has_dk = TransactionDetail.objects.exists()
    has_akun = MasterAkun.objects.exists()
    
    status_pisah_akun = "Perlu implementasi export" if (has_dk and has_akun) else "Menunggu data"
    status_class = "orange" if status_pisah_akun == "Menunggu data" else "blue"

    context.update({
        "page_title": "Laporan",
        "status_pisah_akun": status_pisah_akun,
        "status_class": status_class,
    })
    return render(request, "reports/index.html", context)
