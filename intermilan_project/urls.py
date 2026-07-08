from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from apps.documents.views import checklist_list


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
    path("sp2d/", include("apps.sp2d.urls")),
    path("dk/", include("apps.dk.urls")),
    path("documents/", include("apps.documents.urls")),
    path("checklist/", checklist_list, name="checklist_alias"),
    path("drpp/", include("apps.drpp.urls")),
    path("paket-spm/", include("apps.paket_spm.urls")),
    path("reports/", include("apps.reports.urls")),
]

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
