import re

text = (
    "URAIAN: Pembayaran 019937.010.512212 NOP 00 "
    "URAIAN: Pembayaran uang lembur PPPK bulan Juni 2026 "
    "JUMLAH PENGELUARAN 83.000,00"
)

pattern = re.compile(
    r"(?:U?RAIAN|KEPERLUAN)(?:\s+PEMBAYARAN)?\s*[:;]?\s*"
    r"(Pembayaran\b.*?)(?="
    r"\s+(?:Semua|JUMLAH\s+PENGELUARAN|Kebenaran\s+perhitungan|U?RAIAN|KEPERLUAN)\b)",
    re.IGNORECASE | re.DOTALL,
)

for match in pattern.finditer(text):
    print("Match:", repr(match.group(1)))
