import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings')
django.setup()

from apps.paket_spm.services import probe_package_identity

file_path = r'C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project\media\tmp\SPM NOMOR 00166T_OttMkTQ.pdf'
identity = probe_package_identity(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm')
ext = identity.get('_extracted') or {}
details = ext.get('page_details', [])
print('Pages count:', len(details))
for idx, p in enumerate(details):
    print(f"Page {idx+1}: type={p.get('page_types')}, conf={p.get('confidence')}, len_text={len(p.get('text', ''))}")
