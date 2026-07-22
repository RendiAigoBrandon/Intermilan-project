import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings')
django.setup()

from apps.core.parsers import parse_spm_pdf
from apps.paket_spm.services import probe_package_identity

file_path = r'C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project\media\tmp\SPM NOMOR 00166T_OttMkTQ.pdf'
identity = probe_package_identity(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm')
ext = identity.pop('_extracted', None)

print(f"Extracted method from probe: {ext.get('method')}")

# Now call parse_spm_pdf with ocr=True and this extracted dictionary
# This should trigger our new needs_ocr_rerun logic and call extract_pdf_text with ocr=True
try:
    parsed = parse_spm_pdf(file_path, ocr=True, extracted=ext, parse_details=True)
    print("parse_spm_pdf success!")
except Exception as e:
    print(f"Error: {e}")
