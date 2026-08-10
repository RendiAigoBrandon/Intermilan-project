# DOMAIN CONTRACT: 15-COLUMN DRAFT D_K

**Version:** 1.0
**Status:** MASTER SOURCE OF TRUTH
**Last Updated:** 2026-08-04

---

## PRIORITY OF TRUTH

1. This document (`DOMAIN_CONTRACT_15_COLUMNS.md`)
2. `apps/core/dk_domain_contract.py`
3. Regression tests in `apps/core/test_dk_domain_contract.py`
4. Implementation code
5. Legacy documentation

---

## EXACT KEY DEFINITION

The unique transaction identity for matching and reconciliation:

```
EXACT_KEY = Satker + Tahun + Nomor SPM + No Kuitansi + Akun
```

**Rules:**
- Satker: 6-digit kode satker (e.g., `019937`)
- Tahun: 4-digit year (e.g., `2026`)
- Nomor SPM: SPM number (e.g., `01077A`)
- No Kuitansi: Receipt number (e.g., `01011/KW/019937/2026`)
- Akun: Account code (e.g., `521111`)

---

## SP2D DOCUMENT TYPE RULES

### GUP_REGULAR
- DRPP **required**
- `no_drpp` must be populated with valid DRPP number
- No special KKP restrictions

### GUP_PNBP
- DRPP **required**
- `no_drpp` must be populated
- Same DRPP requirements as GUP_REGULAR

### GUP_KKP
- Uses `KKP_PAYMENT_LIST` format
- `no_drpp` must be **null**
- **FORBIDDEN values:** `-`, `TANPA_DRPP`, `N/A`, empty string
- No DRPP document involved

---

## SOURCE PRIORITY (Highest to Lowest)

| Priority | Source | Description |
|---------|--------|-------------|
| 1 | `manual_confirmed` | User explicitly confirmed value |
| 2 | `ocr_labeled` | OCR from correctly-labeled page/line |
| 3 | `sp2d_enrichment` | SP2D exact-match enrichment for equivalent fields |
| 4 | `derived` | Calculated/derived from other values |
| 5 | `null_review` | No evidence found - null with REVIEW flag |

**Rule:** Higher priority **cannot** be overwritten by lower priority.

---

## ZERO VS NULL SEMANTICS

This is a critical domain invariant.

| Value | Meaning | Evidence Required |
|-------|---------|------------------|
| `0` | Explicit label "Rp0" or "0" found | Yes - OCR must show the label |
| `null` | No evidence found | No evidence |
| `""` | **FORBIDDEN** | N/A |
| `"-"` | **FORBIDDEN** | N/A |

**Domain Invariant:**
```
0 ≠ null
```

**Examples:**
- Label "PPh21: Rp0" found → `pph21 = 0`
- No PPh21 label on receipt → `pph21 = null + REVIEW`
- Balance being 0 is **NOT** evidence of tax/pph completeness

---

## COLUMN DEFINITIONS

### Column 1: Helper

| Property | Value |
|----------|-------|
| Ordinal | 1 |
| Internal Name | `helper` |
| Display Label | Helper |
| Data Type | String (derived) |
| Editable | **No (Read-only)** |
| Source Priority | `derived` |

**Derivation:** `akun + no_kuitansi`

**Purpose:** Concatenation helper for display/search, not stored in DB.

---

### Column 2: Akun

| Property | Value |
|----------|-------|
| Ordinal | 2 |
| Internal Name | `akun` |
| Display Label | Akun |
| Data Type | String (account code) |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Validation:**
- Must exist in `MasterAkun` table
- Required for commit

---

### Column 3: Bulan SP2D

| Property | Value |
|----------|-------|
| Ordinal | 3 |
| Internal Name | `bulan_sp2d` |
| Display Label | Bulan SP2D |
| Data Type | Integer (1-12) |
| Editable | Yes |
| Source Priority | `sp2d_enrichment` (primary) |

**Null Policy:** Accept `null` with REVIEW if SP2D not available.

---

### Column 4: Cara Pembayaran

| Property | Value |
|----------|-------|
| Ordinal | 4 |
| Internal Name | `cara_pembayaran` |
| Display Label | Cara Pembayaran |
| Data Type | String (UP/TUP/GUP/KKP) |
| Editable | Yes |
| Source Priority | `sp2d_enrichment` (primary) |

**Valid Values:** `UP`, `TUP`, `GUP`, `KKP`

---

### Column 5: Nomor SPM

| Property | Value |
|----------|-------|
| Ordinal | 5 |
| Internal Name | `nomor_spm` |
| Display Label | Nomor SPM |
| Data Type | String |
| Editable | Yes |
| Source Priority | `sp2d_enrichment` (primary) |

**Required for:** Draft and Commit

---

### Column 6: Tanggal SPM

| Property | Value |
|----------|-------|
| Ordinal | 6 |
| Internal Name | `tanggal_spm` |
| Display Label | Tanggal SPM |
| Data Type | Date |
| Editable | Yes |
| Source Priority | `sp2d_enrichment` (primary) |

**CRITICAL RULE:**
```
tanggal_spm MUST NOT be sourced from tgl_sp2d (SP2D date)
tanggal_spm != tgl_sp2d
```

**Rationale:** SPM and SP2D are separate documents with different dates.

---

### Column 7: Jenis SPM

| Property | Value |
|----------|-------|
| Ordinal | 7 |
| Internal Name | `jenis_spm` |
| Display Label | Jenis SPM |
| Data Type | String (GUP/UP/TUP) |
| Editable | Yes |
| Source Priority | `sp2d_enrichment` (primary) |

---

### Column 8: No Kuitansi

| Property | Value |
|----------|-------|
| Ordinal | 8 |
| Internal Name | `no_kuitansi` |
| Display Label | No Kuitansi |
| Data Type | String |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Format:** `NNNNN/KW/XXXXXX/YYYY`

**Null Policy:**
- **GUP_REGULAR/PNBP:** Accept `null` with REVIEW if receipt invalid
- **GUP_KKP:** Typically `null` (no receipt in KKP)

**Invalid Receipt Handling:**
- Raw token preserved as evidence
- Normalized `no_kuitansi = null`
- Does NOT enter exact key matching

---

### Column 9: No DRPP

| Property | Value |
|----------|-------|
| Ordinal | 9 |
| Internal Name | `no_drpp` |
| Display Label | No DRPP |
| Data Type | String |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Document Type Rules:**

| Type | Required | Null Policy |
|------|---------|------------|
| GUP_REGULAR | **Required** | REVIEW if null |
| GUP_PNBP | **Required** | REVIEW if null |
| GUP_KKP | **MUST BE NULL** | See below |

**GUP_KKP Forbidden Values:**
```
no_drpp MUST NOT be:
- "-"
- "TANPA_DRPP"
- "N/A"
- "" (empty)
- Any placeholder
```

If KKP is detected, `no_drpp` must be explicitly set to `null`.

---

### Column 10: Deskripsi

| Property | Value |
|----------|-------|
| Ordinal | 10 |
| Internal Name | `deskripsi` |
| Display Label | Deskripsi |
| Data Type | Text |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Null Policy:** Accept `null` with REVIEW

---

### Column 11: Nilai Bruto

| Property | Value |
|----------|-------|
| Ordinal | 11 |
| Internal Name | `nilai_bruto` |
| Display Label | Nilai Bruto |
| Data Type | Decimal |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Required for:** Draft and Commit
**Validation:** Must be >= 0

---

### Column 12: Nilai Netto

| Property | Value |
|----------|-------|
| Ordinal | 12 |
| Internal Name | `nilai_netto` |
| Display Label | Nilai Netto |
| Data Type | Decimal |
| Editable | Yes |
| Source Priority | `derived` or `ocr_labeled` |

**Derivation:** `nilai_bruto - fp - pph21`

**Note:** Balance being 0 does NOT prove document completeness.

---

### Column 13: Pembebanan

| Property | Value |
|----------|-------|
| Ordinal | 13 |
| Internal Name | `pembebanan` |
| Display Label | Pembebanan |
| Data Type | String (KODE_BELANJA) |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Format:** `NNNN.MMM.XXX.XXX.XXXXXX`

**Normalization:**
- Mixed separators (`,` and `.`) normalized to `.`
- Double separators collapsed
- Must match `KODE_BELANJA` pattern

**Null Policy:** Accept `null` with REVIEW

---

### Column 14: FP

| Property | Value |
|----------|-------|
| Ordinal | 14 |
| Internal Name | `fp` |
| Display Label | FP |
| Data Type | Decimal |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Zero Semantics:**
- `fp = 0`: Explicit "FP: Rp0" or "Faktur Pajak: 0" label found
- `fp = null`: No evidence found → REVIEW

**Note:** FP here means "Faktur Pajak" (tax invoice), not "Fiscal Position".

---

### Column 15: PPh21

| Property | Value |
|----------|-------|
| Ordinal | 15 |
| Internal Name | `pph21` |
| Display Label | PPh21 |
| Data Type | Decimal |
| Editable | Yes |
| Source Priority | `ocr_labeled` (primary) |

**Zero Semantics:**
- `pph21 = 0`: Explicit "PPh21: Rp0" label found
- `pph21 = null`: No evidence found → REVIEW

**Note:** Balance being 0 does NOT prove PPh21 completeness.

---

## REVIEW METADATA (Separate from 15 Columns)

These fields track evidence and review status, **NOT** counted as part of the 15 columns.

**IMPORTANT:** The D_K row has exactly 15 top-level keys. Review metadata is stored separately.

### Factory Functions

```python
# Returns exactly 15 keys (the D_K row)
draft_row = build_empty_dk_draft_row()

# Returns metadata (separate from row)
review_metadata = build_empty_dk_review_metadata()

# Returns container with both
full_draft = build_empty_dk_draft()
# {
#     "row": {...},           # 15 columns
#     "review_metadata": {...}  # metadata (NOT column 16)
# }
```

| Field | Type | Description |
|-------|------|-------------|
| `field_status` | Dict | Per-field status: EXACT/REVIEW/MISSING/WRONG |
| `field_source` | Dict | Per-field source: manual/ocr/sp2d/derived/null |
| `field_evidence` | Dict | Evidence text or reference |
| `requires_review` | Boolean | True if any field needs review |
| `review_reasons` | List | List of review reason strings |

---

## AGGREGATION STATUS

| Status | Meaning | Exit Code |
|--------|---------|-----------|
| PASS | All fields exact, no unresolved | 0 |
| REVIEW | Has unresolved/manual-review fields, no WRONG | 0 |
| FAIL | Has WRONG fields or hard failures | 1 |

**Hard Failures (always FAIL):**
- `canonical WRONG` field detected
- `EXTRA canonical transaction` detected
- `MISSING` transaction without unresolved counterpart

---

## INVALID RECEIPT HANDLING

When a receipt is marked invalid (OCR ambiguity):

1. Raw receipt token preserved as evidence
2. `no_kuitansi` normalized to `null`
3. Receipt does NOT enter exact key matching
4. Receipt added to `unresolved_bundle_ids`
5. Status: `UNRESOLVED/REVIEW`

---

## TESTING CONTRACT

Any test that violates this contract is **wrong**, not the implementation.

If test expectations conflict with this document:
1. Audit the test fixture
2. Fix the fixture to match contract
3. Do NOT change implementation to match test

---

## PHASE 6 ROADMAP

### Phase 6A (Current)
- [x] Create DOMAIN_CONTRACT_15_COLUMNS.md
- [x] Create dk_domain_contract.py
- [x] Create build_empty_dk_draft_row()
- [x] Create gap matrix
- [ ] Integrate with preview

### Phase 6B
- [ ] Integrate ai_semantic_* with web upload
- [ ] 15-column preview always available
- [ ] Validator respects source priority

### Phase 6C
- [ ] Manual confirmation flow
- [ ] REVIEW status save capability
- [ ] Commit with REVIEW warnings

---

## REFERENCES

- `apps/core/dk_domain_contract.py` - Machine-readable contract
- `apps/core/test_dk_domain_contract.py` - Contract validation tests
- `apps/dk/models.py` - TransactionDetail model
- `apps/paket_spm/models.py` - PaketSPMPreviewItem model
