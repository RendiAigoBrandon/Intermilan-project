# END-TO-END 15-COLUMN GAP MATRIX

**Version:** 1.0
**Date:** 2026-08-04

---

## PIPELINE OVERVIEW

```
PDF Upload
    ↓
Parser (parse_drpp_upload_batch / parse_paket_spm_zip)
    ↓
PaketSPMUpload.parsed_data
    ↓
Preview (PaketSPMPreviewItem)
    ↓
Manual Edit
    ↓
Draft Save
    ↓
Validator
    ↓
Commit
    ↓
TransactionDetail
    ↓
Dashboard
```

---

## GAP MATRIX: 15 COLUMNS

| # | Field | Parser Source | parsed_data Key | PaketSPMPreviewItem | Editable | Validator | TransactionDetail | Dashboard | Status |
|---|-------|---------------|----------------|---------------------|----------|-----------|-----------------|----------|--------|
| 1 | helper | derived | N/A | helper | **No (R)** | N/A | @property | Read | ✅ COMPLETE |
| 2 | akun | OCR | kw_items[].akun | akun | Yes | required | akun | akun | ✅ COMPLETE |
| 3 | bulan_sp2d | SP2D | N/A | bulan_sp2d | Yes | - | bulan_sp2d | bulan_sp2d | ✅ COMPLETE |
| 4 | cara_pembayaran | SP2D | N/A | cara_pembayaran | Yes | - | cara_pembayaran | - | ⚠️ PARTIAL |
| 5 | nomor_spm | SP2D | spm.nomor_spm | nomor_spm | Yes | - | nomor_spm | - | ⚠️ PARTIAL |
| 6 | tanggal_spm | SP2D | **???** | tanggal_spm | Yes | - | tanggal_spm | - | ❌ MISSING/GAP |
| 7 | jenis_spm | SP2D | **???** | jenis_spm | Yes | - | jenis_spm | - | ⚠️ PARTIAL |
| 8 | no_kuitansi | OCR | kw_items[].no_kuitansi | no_kuitansi | Yes | - | no_kuitansi | no_kuitansi | ✅ COMPLETE |
| 9 | no_drpp | OCR | drpp.nomor_drpp | no_drpp | Yes | conditional | no_drpp | no_drpp | ⚠️ PARTIAL |
| 10 | deskripsi | OCR | kw_items[].deskripsi | deskripsi | Yes | - | deskripsi | - | ⚠️ PARTIAL |
| 11 | nilai_bruto | OCR | kw_items[].nilai_bruto | nilai_bruto | Yes | required | nilai_bruto | nilai_bruto | ✅ COMPLETE |
| 12 | nilai_netto | derived | N/A | nilai_netto | Yes | - | nilai_netto | nilai_netto | ⚠️ PARTIAL |
| 13 | pembebanan | OCR | kw_items[].pembebanan | pembebanan | Yes | - | pembebanan | - | ⚠️ PARTIAL |
| 14 | fp | OCR | kw_items[].fp | fp | Yes | - | fp | fp | ⚠️ PARTIAL |
| 15 | pph21 | OCR | kw_items[].pph21 | pph21 | Yes | - | pph21 | pph21 | ⚠️ PARTIAL |

**Legend:**
- ✅ COMPLETE: Field fully implemented
- ⚠️ PARTIAL: Field exists but may have issues
- ❌ MISSING/GAP: Field missing or major issue
- **No (R)**: Not editable, read-only

---

## DETAILED GAP ANALYSIS

### Gap 1: tanggal_spm from tgl_sp2d

**Issue:** CRITICAL - tanggal_spm may be sourced from tgl_sp2d

**Contract Rule:**
```
tanggal_spm MUST NOT be sourced from tgl_sp2d
```

**Current State:**
- Parser may use `tgl_sp2d` date for `tanggal_spm`
- This violates the domain contract

**Required Fix:**
- tanggal_spm must come from SPM document date
- SPM and SP2D are separate documents with different dates
- Must validate that tanggal_spm != tgl_sp2d

---

### Gap 2: GUP_PNBP Falls to OTHER

**Issue:** Document type detection may fall to OTHER

**Contract Types:**
- GUP_REGULAR (DRPP required)
- GUP_PNBP (DRPP required)
- GUP_KKP (no DRPP, uses KKP_PAYMENT_LIST)

**Current State:**
- Classification may not properly detect GUP_PNBP
- May fall to OTHER type

**Required Fix:**
- Implement GUP_PNBP detection
- Enforce DRPP requirement for GUP_PNBP
- Prevent fallthrough to OTHER

---

### Gap 3: KKP no_drpp Handling

**Issue:** KKP may use `no_drpp = "-"`

**Contract Rule:**
```
GUP_KKP: no_drpp MUST BE NULL
Forbidden: "-", "TANPA_DRPP", "N/A", ""
```

**Current State:**
- Parser may set `no_drpp = "-"` for KKP
- This violates the domain contract

**Required Fix:**
- Detect KKP payment type
- Set `no_drpp = null` for KKP
- Reject "-" or "TANPA_DRPP" values

---

### Gap 4: FP Type Ambiguity

**Issue:** FP field type unclear

**Contract:**
```
FP = Faktur Pajak (tax invoice amount)
Type: Decimal
```

**Current State:**
- PaketSPMPreviewItem.fp = CharField (string)
- TransactionDetail.fp = CharField (string)
- No validation for numeric type

**Required Fix:**
- Define FP as Decimal type
- Validate numeric format
- Apply zero/null semantics

---

### Gap 5: ai_semantic_* Not Integrated

**Issue:** AI semantic bundle/resolver not used in web upload

**Contract Integration Path:**
```
PDF → ai_semantic_bundles → ai_semantic_resolver → PaketSPMPreviewItem
```

**Current State:**
- ai_semantic_* modules exist but not connected to web
- Parser uses legacy OCR approach

**Phase 6B Task:**
- Integrate ai_semantic_bundles with upload
- Use probe_ai_semantic_bundles command pattern
- Connect to PaketSPMPreviewItem fields

---

### Gap 6: Preview Always Has 15 Fields

**Issue:** Preview may not guarantee 15 fields

**Contract:**
```
Preview must always have 15 columns
Missing fields = null with REVIEW flag
```

**Required Fix:**
- Use build_empty_dk_draft_row() for preview initialization
- Ensure all 15 fields present
- Add review_metadata to preview

---

### Gap 7: Draft REVIEW Status Save

**Issue:** REVIEW status may not be saveable

**Contract:**
```
REVIEW status can be saved
Commit with REVIEW = non-zero exit
```

**Required Fix:**
- Allow DRAFT with REVIEW fields to be saved
- Track review_reasons in database
- Display REVIEW warnings on commit attempt

---

### Gap 8: Dashboard Only Reads Committed

**Issue:** Dashboard may read all TransactionDetail

**Contract:**
```
Dashboard reads committed TransactionDetail only
```

**Required Fix:**
- Filter dashboard to only show FINAL/DIARSIPKAN status
- Add status filter in dashboard queries

---

## ai_semantic_* INTEGRATION STATUS

| Module | Status | Notes |
|--------|--------|-------|
| ai_semantic_bundles | ✅ Exists | Transaction bundling |
| ai_semantic_resolver | ✅ Exists | Semantic field resolution |
| ai_semantic_candidates | ✅ Exists | Candidate extraction |
| ai_semantic_wire | ✅ Exists | Wire protocol |
| ai_synthetic_data | ✅ Exists | Test data generation |
| ai_synthetic_holdout | ✅ Exists | Holdout cases |
| probe_ai_semantic_bundles | ✅ Exists | CLI command |
| **Web Integration** | ❌ NOT DONE | Phase 6B task |

---

## PHASE 6B PRIORITIES

1. **Integrate ai_semantic_* with upload flow**
2. **Fix tanggal_spm sourcing (not from tgl_sp2d)**
3. **Implement 15-column preview with build_empty_dk_draft_row()**
4. **Fix KKP no_drpp = null**
5. **Implement GUP_PNBP detection**
6. **Add REVIEW status save capability**
7. **Validate FP as Decimal type**

---

## DATABASE MAPPING

### TransactionDetail (apps/dk/models.py)

| Field | Type | 15-Col # | Notes |
|-------|------|-----------|-------|
| satker_code | Char | - | Metadata |
| akun | Char | 2 | Required |
| bulan_sp2d | Integer | 3 | From SP2D |
| cara_pembayaran | Char | 4 | From SP2D |
| nomor_spm | Char | 5 | From SP2D |
| tanggal_spm | Date | 6 | **NOT from tgl_sp2d** |
| jenis_spm | Char | 7 | From SP2D |
| no_kuitansi | Char | 8 | From OCR |
| no_drpp | Char | 9 | From OCR |
| deskripsi | Text | 10 | From OCR |
| nilai_bruto | Decimal | 11 | From OCR |
| nilai_netto | Decimal | 12 | Derived |
| pembebanan | Char | 13 | From OCR |
| fp | Char | 14 | **Should be Decimal** |
| pph21 | Decimal | 15 | From OCR |
| status_detail | Choice | - | Workflow status |
| drpp_status | Choice | - | DRPP validation |

### PaketSPMPreviewItem (apps/paket_spm/models.py)

| Field | Type | 15-Col # | Notes |
|-------|------|-----------|-------|
| helper | Char | 1 | Derived |
| akun | Char | 2 | - |
| bulan_sp2d | Integer | 3 | - |
| cara_pembayaran | Char | 4 | - |
| nomor_spm | Char | 5 | - |
| tanggal_spm | Date | 6 | **NOT from tgl_sp2d** |
| jenis_spm | Char | 7 | - |
| no_kuitansi | Char | 8 | - |
| no_drpp | Char | 9 | - |
| deskripsi | Text | 10 | - |
| nilai_bruto | Decimal | 11 | - |
| nilai_netto | Decimal | 12 | - |
| pembebanan | Char | 13 | - |
| fp | Char | 14 | **Should be Decimal** |
| pph21 | Decimal | 15 | - |
| status | Choice | - | Workflow status |

---

## REFERENCES

- Domain Contract: `docs/DOMAIN_CONTRACT_15_COLUMNS.md`
- Contract Code: `apps/core/dk_domain_contract.py`
- Gap Matrix: This document
