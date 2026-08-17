# Gunicorn configuration for INTERMILAN production deployment.
# Loaded automatically when gunicorn starts from the project root.
# https://docs.gunicorn.org/en/stable/configure.html

# Increase worker timeout to accommodate long OCR operations on large SPM PDFs.
# Default is 30s; a 13-page PDF with 11 OCR pages takes ~36s locally.
# Gunicorn configuration for INTERMILAN production deployment.
# Loaded automatically when gunicorn starts from the project root.
# https://docs.gunicorn.org/en/stable/configure.html

# Increase worker timeout to accommodate long OCR operations on large SPM PDFs.
# Default is 30s; a 13-page PDF with 11 OCR pages takes ~36s locally.
# 180s gives headroom for production server variability.
timeout = 180

# Keep graceful for worker shutdown
graceful_timeout = 30

def on_starting(server):
    try:
        import os
        import sys
        from urllib.parse import urlparse

        # 1. Membaca DATABASE_URL dari runtime production
        db_url = os.environ.get("DATABASE_URL")
        db_host = "Unknown"
        db_name = "Unknown"
        if db_url:
            parsed = urlparse(db_url)
            db_host = parsed.hostname
            db_name = parsed.path.lstrip("/")
        
        django_settings = os.environ.get("DJANGO_SETTINGS_MODULE", "Unknown")

        print("\n" + "="*50)
        print("DATABASE HOST: " + str(db_host))
        print("DATABASE NAME: " + str(db_name))
        print("DJANGO SETTINGS: " + str(django_settings))
        print("\nTRANSACTION COUNTS:")

        # Initialize Django to run ORM
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.production")
        import django
        django.setup()

        from apps.core.models import TransactionPackage
        from apps.sp2d.models import SP2DRaw
        from apps.dk.models import TransactionDetail
        from apps.drpp.models import DRPPUpload
        from apps.paket_spm.models import PaketSPMUpload
        from apps.documents.models import DocumentUpload
        
        try:
            from apps.core.models import ActiveParentSession
        except ImportError:
            ActiveParentSession = None

        counts = {
            "SP2DRaw": SP2DRaw.objects.count(),
            "TransactionDetail": TransactionDetail.objects.count(),
            "DRPPUpload": DRPPUpload.objects.count(),
            "PaketSPMUpload": PaketSPMUpload.objects.count(),
            "DocumentUpload": DocumentUpload.objects.count(),
            "TransactionPackage": TransactionPackage.objects.count(),
        }
        if ActiveParentSession:
            counts["ActiveParentSession"] = ActiveParentSession.objects.count()

        for model_name, count in counts.items():
            print(f"{model_name} = {count}")

        print("")
        total = sum(counts.values())
        if total == 0:
            print("PRODUCTION DATABASE CLEAN")
        else:
            # Jika ada data tampilkan id dan satker saja untuk menghindari log sensitif
            if counts["SP2DRaw"] > 0:
                for s in SP2DRaw.objects.all()[:5]:
                    print(f"- model: SP2DRaw | id: {s.id} | satker_code: {getattr(s, 'satker_code', 'N/A')}")
            if counts["TransactionDetail"] > 0:
                for td in TransactionDetail.objects.all()[:5]:
                    print(f"- model: TransactionDetail | id: {td.id} | satker_code: {getattr(td, 'satker_code', 'N/A')}")
            if counts["TransactionPackage"] > 0:
                for tp in TransactionPackage.objects.all()[:5]:
                    print(f"- model: TransactionPackage | id: {tp.id} | satker_code: {getattr(tp, 'satker_code', 'N/A')}")
            if counts["DRPPUpload"] > 0:
                for d in DRPPUpload.objects.all()[:5]:
                    print(f"- model: DRPPUpload | id: {d.id} | satker_code: {getattr(d, 'satker_code', 'N/A')}")
            if counts["PaketSPMUpload"] > 0:
                for p in PaketSPMUpload.objects.all()[:5]:
                    print(f"- model: PaketSPMUpload | id: {p.id} | satker_code: {getattr(p, 'satker_code', 'N/A')}")
            if counts["DocumentUpload"] > 0:
                for doc in DocumentUpload.objects.all()[:5]:
                    print(f"- model: DocumentUpload | id: {doc.id} | satker_code: {getattr(doc, 'satker_code', 'N/A')}")

        print("="*50 + "\n")
    except Exception as e:
        print(f"\n[AUDIT ERROR] Gagal menjalankan audit hook: {e}\n")
