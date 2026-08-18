import re

def normalize_nomor_spm(nomor):
    """
    Menghapus suffix huruf (seperti A, T, B, AB, dll) dari akhir nomor SPM.
    Contoh:
    '00166A' -> '00166'
    '00166T' -> '00166'
    '00166AB' -> '00166'
    """
    if not nomor:
        return ""
    nomor_str = str(nomor).strip()
    return re.sub(r'[a-zA-Z]+$', '', nomor_str)

def regex_for_nomor_spm(nomor):
    """
    Menghasilkan regex untuk pencarian ORM yang aman, hanya mengizinkan suffix huruf.
    Contoh: '00166' -> '^00166[a-zA-Z]*$'
    """
    base = normalize_nomor_spm(nomor)
    if not base:
        return ""
    return rf"^{re.escape(base)}[a-zA-Z]*$"
