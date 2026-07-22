import os
import sys
import time

def trace_calls(frame, event, arg):
    if event == "call":
        func_name = frame.f_code.co_name
        if "parsers" in frame.f_code.co_filename and func_name in ["parse_spm_pdf", "parse_position_detail_items", "ocr_page_table_variants", "parse_detail_sp2d_rows_by_crop", "ocr_cell_text"]:
            print(f"[{time.strftime('%H:%M:%S')}] CALL {func_name} in {os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}", flush=True)
    return trace_calls

sys.settrace(trace_calls)

from apps.paket_spm.package_views import parse_uploaded_package
from apps.paket_spm.services import probe_package_identity

file_path = r'C:\Users\muall\Documents\INTERMILAN PROJECT\intermilan_project\media\tmp\SPM NOMOR 00166T_OttMkTQ.pdf'
print(f"[{time.strftime('%H:%M:%S')}] Probing...")
identity = probe_package_identity(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm')
extracted_identity_ocr = identity.pop('_extracted', None)
print(f"[{time.strftime('%H:%M:%S')}] Probe done. Extracted passed: {extracted_identity_ocr is not None}")
print(f"[{time.strftime('%H:%M:%S')}] Calling parse_uploaded_package...")

t0 = time.time()
parsed = parse_uploaded_package(file_path, 'SPM NOMOR 00166T_OttMkTQ.pdf', kind='paket_spm', extracted=extracted_identity_ocr)
t1 = time.time()

print(f"[{time.strftime('%H:%M:%S')}] Done in {t1 - t0:.2f}s! Status: {parsed.get('ok')}")
