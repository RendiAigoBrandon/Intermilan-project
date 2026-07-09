import re

with open('apps/paket_spm/views.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_paket_spm_list = '''
@login_required
def paket_spm_list(request):
    if request.method == "POST":
        upload_file = request.FILES.get("file_paket")
        upload_files = request.FILES.getlist("document_files")
        if not upload_files:
            upload_files = request.FILES.getlist("file_paket")
            upload_file = None
        if not upload_file and not upload_files:
            messages.error(request, "Harap pilih PDF, folder PDF, atau ZIP paket SPM.")
            return redirect("paket_spm:list")
        validation_error = validate_paket_upload(upload_file, upload_files)
        if validation_error:
            messages.error(request, validation_error)
            return redirect("paket_spm:list")

        tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        fs = FileSystemStorage(location=tmp_dir)

        if upload_files and len(upload_files) == 1 and upload_files[0].name.lower().endswith((".pdf", ".zip")):
            single_upload = upload_files[0]
            lower_name = single_upload.name.lower()
            filename = fs.save(single_upload.name, single_upload)
            original_filename = single_upload.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"
        elif upload_files:
            filename = save_many_files_as_zip(fs, upload_files)
            original_filename = filename
            kind = "zip"
        else:
            lower_name = upload_file.name.lower()
            filename = fs.save(upload_file.name, upload_file)
            original_filename = upload_file.name
            kind = "zip" if lower_name.endswith(".zip") else "pdf"

        file_path = fs.path(filename)
        use_ocr = bool(request.POST.get("use_ocr"))
        
        # 1. Parsing di POST
        try:
            if kind == "pdf":
                text_probe = extract_pdf_text(file_path, ocr=False)
                doc_type = classify_document(original_filename, "\\n".join(text_probe["pages"]))
                if doc_type == "DRPP" or doc_type == "KW":
                    drpp = parse_drpp_pdf(file_path, ocr=use_ocr)
                    spm = None
                else:
                    doc_type = "SPM"
                    spm = parse_spm_pdf(file_path, ocr=use_ocr)
                    drpp = None
                parsed = {
                    "ok": bool(
                        (spm and spm["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (spm["metadata"].get("nomor_spm") or spm["akun_rows"]))
                        or (drpp and drpp["status"] in {"parsed_text", "parsed_ocr", "needs_manual_review"} and (drpp["metadata"].get("nomor_drpp") or drpp["items"]))
                    ),
                    "files": [{
                        "file_name": original_filename,
                        "type": doc_type,
                        "status": "extracted",
                        "parse_status": (spm or drpp)["status"],
                        "method": (spm or drpp)["method"],
                        "warnings": (spm or drpp)["warnings"],
                    }],
                    "spm": spm,
                    "drpp": drpp,
                    "drpps": [drpp] if drpp else [],
                    "kw_by_drpp": {drpp["metadata"].get("nomor_drpp", "DRPP"): drpp.get("items", [])} if drpp else {},
                    "kw_items": drpp.get("items", []) if drpp else [],
                    "warnings": [],
                    "temp_dir": "",
                }
            else:
                parsed = parse_paket_spm_zip(file_path, ocr=use_ocr)
        except Exception as exc:
            parsed = {"ok": False, "files": [], "spm": None, "drpp": None, "kw_items": [], "warnings": [str(exc)], "temp_dir": ""}

        # 2. Simpan ke database sebagai DRAFT
        spm_meta = (parsed.get("spm") or {}).get("metadata", {})
        drpp_meta = (parsed.get("drpp") or {}).get("metadata", {})
        tahun = int(request.POST.get("tahun")) if str(request.POST.get("tahun", "")).isdigit() else None
        bulan = parse_month(request.POST.get("bulan", ""))
        satker = str(request.POST.get("satker_code") or spm_meta.get("satker_code") or "")[:32]
        
        paket = PaketSPMUpload(
            original_filename=original_filename,
            folder_path=parsed.get("temp_dir", ""),
            nomor_spm=str(spm_meta.get("nomor_spm") or drpp_meta.get("nomor_spm") or "")[:100],
            nomor_sp2d=str(spm_meta.get("nomor_sp2d") or "")[:100],
            nomor_invoice=str(spm_meta.get("nomor_invoice") or "")[:100],
            satker_code=satker,
            tahun=tahun,
            bulan=bulan,
            jenis_spm_asli=str(spm_meta.get("jenis_spm") or "")[:100],
            jenis_spm_label=str(spm_meta.get("jenis_spm") or "")[:100],
            tanggal_spm=spm_meta.get("tanggal_spm"),
            nilai_spm=spm_meta.get("total_pembayaran") or Decimal("0"),
            total_rincian_bruto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
            total_rincian_netto=sum((item.get("jumlah") or Decimal("0") for item in parsed.get("kw_items", [])), Decimal("0")),
            status=PaketSPMUpload.Status.PREVIEW,
            uploaded_by=request.user,
            parsed_data=parsed,
        )
        with open(file_path, "rb") as zip_file:
            paket.zip_file.save(original_filename, File(zip_file), save=False)
        paket.save()
        
        request.session["paket_spm_preview_id"] = paket.id
        request.session["sp2d_raw_id"] = request.POST.get("sp2d_raw_id", "")
        
        print(f"[INTERMILAN PaketSPM Upload] Saved as PREVIEW id={paket.id}", flush=True)
        return redirect("paket_spm:preview")
'''

start = content.find('@login_required\\ndef paket_spm_list(request):')
end = content.find('rows = filter_by_satker(PaketSPMUpload.objects.select_related("uploaded_by"), request.user)', start)

if start != -1 and end != -1:
    new_content = content[:start] + new_paket_spm_list + '\\n    ' + content[end:]
    with open('apps/paket_spm/views.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Replaced paket_spm_list")
else:
    print("Could not find bounds")
