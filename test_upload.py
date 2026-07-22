import time
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings')
django.setup()

from apps.paket_spm.package_views import parse_uploaded_package
from apps.paket_spm.services import probe_package_identity

file_path = r'C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project\media\tmp\SPM NOMOR 00166T_OttMkTQ.pdf'
identity = probe_package_identity(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm')
extracted_identity_ocr = identity.pop('_extracted', None)

t0 = time.time()
parsed = parse_uploaded_package(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm', extracted=extracted_identity_ocr)
t1 = time.time()

print(f'Done in {t1 - t0:.2f}s! Status: {parsed.get("ok")}')
