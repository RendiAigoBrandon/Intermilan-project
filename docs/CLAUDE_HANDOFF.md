# CLAUDE_HANDOFF.md

## Fase 5F-A: Pre-flight Checklist

### Status: PRE-FLIGHT COMPLETE

### Files Modified This Session

| File | Status | Purpose |
|------|--------|---------|
| `apps/core/management/commands/probe_ai_semantic_bundles.py` | **UNTRACKED** | Command rewrite |
| `apps/core/test_probe_real_holdout.py` | **UNTRACKED** | Comprehensive tests |
| `scratch/real_holdout/input/ground_truth_DRPP_00107.json` | tracked (clean) | Transaction-level GT |

### Guard Enforcement (ACTIVE)

```python
# In handle() — MANDATORY for --pdf mode
if not options["no_ollama"]:
    raise CommandError("guard_no_ollama_required")
if not options["no_db"]:
    raise CommandError("guard_no_db_required")
```

- Flag `--no-ollama` **required** for real holdout
- Flag `--no-db` **required** for real holdout
- No Ollama transport imported or called in command
- No ORM query/write in pipeline

### Ground Truth Schema (real-holdout-ground-truth-v1)

```json
{
  "schema_version": "real-holdout-ground-truth-v1",
  "document": { "nomor_drpp", "nomor_spm", "printed_total", ... },
  "transactions": [
    { "receipt", "account", "gross", "net", "tax", "fp", "pembebanan", ... }
  ]
}
```

**Required fields per transaction:**
- `receipt` (string, required)
- `account` (string, required)
- `gross` (integer, required)

**Exact key:** `receipt + account`

### Evaluator Field Comparison

Exact key: `receipt + account`

Field status: `EXACT | MISSING | EXTRA | WRONG | REVIEW`

Compared fields:
- Document: `nomor_drpp`, `nomor_spm`, `printed_total`
- Transaction: `gross`, `net`, `tax`, `fp`, `pembebanan`, `description`
- Count: `transaction_count`

### evaluation.json

**Always written** — including on mismatch, pipeline failure, and resolver invalid.

Exit behavior:
- `exact_match=True` → exit 0
- Any mismatch, unresolved, ambiguity invalid, or pipeline failure → non-zero

### validate_ground_truth() Checks (BEFORE OCR)

1. File exists
2. Valid JSON
3. `schema_version == "real-holdout-ground-truth-v1"`
4. `document` field present
5. `transactions` non-empty
6. `expected_transaction_count == len(transactions)`
7. Each transaction: `receipt` present
8. Each transaction: `account` present
9. Each transaction: `gross` not null
10. No duplicate `receipt+account` keys

### Artifact Outputs (scratch/real_holdout/output/)

1. `page_manifest.json` — raw OCR pages
2. `line_manifest.json` — per-line structured text
3. `raw_candidates.json` — all extracted candidates
4. `ai_candidate_view.json` — filtered for AI consumption
5. `bundle_manifest.json` — transaction bundles (Tier 1/2)
6. `document_selection.json` — DRPP/SPM/total selection
7. `deterministic_gate.json` — readiness gate result
8. `canonical_mapping.json` — expanded canonical mapping
9. `resolver_result.json` — resolved with source values
10. `evaluation.json` — **always written** final evaluation

### Baseline Command (Phase 5F-A)

```bash
python manage.py probe_ai_semantic_bundles \
    --pdf scratch/real_holdout/input/DRPP_00107_KW_01011-01014_SCAN.pdf \
    --ground-truth scratch/real_holdout/input/ground_truth_DRPP_00107.json \
    --no-ollama \
    --no-db
```

### Test Results

```
Ran 24 tests in 0.393s — OK
System check: 0 issues
py_compile: OK
```

### What's NOT Implemented (Out of Scope)

- Balance = 0 blocking
- GUP_KKP / KKP_PAYMENT_LIST / no_drpp null
- D_K production format
- Views, upload, preview
- Database ORM writes
- Ollama integration
- Real-world PDF execution (pending Phase 5F-A baseline)

### Next Instruction

Run baseline PDF control:
```
python manage.py probe_ai_semantic_bundles \
    --pdf scratch/real_holdout/input/DRPP_00107_KW_01011-01014_SCAN.pdf \
    --ground-truth scratch/real_holdout/input/ground_truth_DRPP_00107.json \
    --no-ollama --no-db
```

Do NOT run real-world PDF (DRPP 00061) until dummy baseline PASS.

## Fase 5F-D: Invalid Receipt Filter + Pembebanan + FP/PPh Fix

### Status: COMPLETE

### Files Changed

| File | Changes |
|-------|----------|
| `ai_semantic_bundles.py` | `expand_selected_bundles_to_canonical` filters `receipt_valid=False`; `evaluate_bundle_readiness` detects invalid receipts |
| `ai_semantic_candidates.py` | pembebanan regex handles mixed-separator OCR |
| `ai_semantic_resolver.py` | reuse_reason backward compat |
| `probe_ai_semantic_bundles.py` | evaluator REVIEW status for invalid receipts |
| `test_ai_semantic_*.py` | regression tests |
| `test_probe_real_holdout.py` | evaluation tests |

### Key Behavior Changes

#### 1. Invalid Receipt (OCR ambiguity)

- `receipt_valid=False` → filtered from `expand_selected_bundles_to_canonical`
- Bundle still constructed (diagnostic), but `selected_bundle_ids` excludes it
- `evaluate_bundle_readiness` sets `deterministic_ready=False` with `invalid_receipt_ocr_ambiguity` reason
- `receipt_valid=False` NOT `requires_ai` — only `requires_review=True`
- Canonical mapping: invalid receipt excluded from transactions list
- Evaluator: marks as REVIEW, reports MISSING for expected key

#### 2. Pembebanan Mixed Separator

Regex updated: `r'\b\d+[.,]+[A-Za-z.0-9]+[.,]+[\d.,]+[.,]+\d{6}\b'`
Handles: `2886,EBA.994.521111`, `2886,.EBA.994,002.521111`, `2886,EBA.994,002.522119`

#### 3. FP/PPh21 Explicit Zero

- Label FP with Rp0 → `fp=0`
- Label PPh21 with Rp0 → `tax=0`
- No label found → `null`
- `reuse_reason` renamed: `gross_net_no_deduction_no_fp`

### Regression Tests Added

- Invalid receipt not in canonical transactions
- Pembebanan mixed-separator canonicalization
- FP/PPh explicit zero handling
- Field-level comparison metrics

### Exit Codes

```
python manage.py test ... → 94 tests OK
manage.py check → 0 issues
py_compile → OK
git diff --check → 0 errors

---

# WAJIB BACA

**docs/DOMAIN_CONTRACT_15_COLUMNS.md** — This is the MASTER SOURCE OF TRUTH for all D_K operations.

**Truth Order:**
1. `docs/DOMAIN_CONTRACT_15_COLUMNS.md`
2. `apps/core/dk_domain_contract.py`
3. `apps/core/test_dk_domain_contract.py`
4. Implementation code
5. Legacy documentation

If tests conflict with contract, fix the tests. Do NOT change contract.

---

## Fase 6A: 15-Column Domain Contract

### Status: COMPLETE

### Files Created

| File | Purpose |
|------|---------|
| `docs/DOMAIN_CONTRACT_15_COLUMNS.md` | Master source of truth |
| `docs/GAP_MATRIX_15_COLUMNS.md` | End-to-end gap analysis |
| `apps/core/dk_domain_contract.py` | Machine-readable contract |
| `apps/core/test_dk_domain_contract.py` | Contract validation tests |
| `build_empty_dk_draft_row()` | Empty draft factory |

### 15 Columns Defined

| # | Field | Type | Required for Draft | Required for Commit |
|---|-------|------|-------------------|-------------------|
| 1 | helper | string | No (derived) | No (derived) |
| 2 | akun | string | Yes | Yes |
| 3 | bulan_sp2d | integer | No | Yes |
| 4 | cara_pembayaran | string | Yes | Yes |
| 5 | nomor_spm | string | Yes | Yes |
| 6 | tanggal_spm | date | Yes | Yes |
| 7 | jenis_spm | string | Yes | Yes |
| 8 | no_kuitansi | string | No | Yes |
| 9 | no_drpp | string | No (conditional) | Yes (conditional) |
| 10 | deskripsi | text | No | Yes |
| 11 | nilai_bruto | decimal | Yes | Yes |
| 12 | nilai_netto | decimal | Yes | Yes |
| 13 | pembebanan | string | Yes | Yes |
| 14 | fp | decimal | No | Yes |
| 15 | pph21 | decimal | No | Yes |

### Key Contract Rules

#### Source Priority (Highest to Lowest)
1. `manual_confirmed`
2. `ocr_labeled`
3. `sp2d_enrichment`
4. `derived`
5. `null_review`

#### Zero vs Null
```
0 = explicit "Rp0" label found
null = no evidence
0 != null (domain invariant)
```

#### Document Types
- **GUP_REGULAR/PNBP**: `no_drpp` required
- **GUP_KKP**: `no_drpp` MUST be null (forbidden: "-", "TANPA_DRPP")

#### Critical Rules
- `tanggal_spm` MUST NOT be from `tgl_sp2d`
- `helper` is read-only (derived)
- Balance 0 is NOT evidence of completeness

### Gap Matrix Summary

| Gap | Status | Phase |
|-----|--------|-------|
| tanggal_spm from tgl_sp2d | ✅ FIXED | 6B-2 |
| GUP_PNBP detection | ✅ INTEGRATED | 6B-2 |
| KKP no_drpp = null | ✅ INTEGRATED | 6B-2 |
| FP type (should be Decimal) | ⚠️ PARTIAL | 6B |
| ai_semantic_* integration | ❌ NOT DONE | 6B |
| 15-column preview | ✅ COMPLETE | 6B-2 |
| REVIEW status save | ✅ COMPLETE | 6B-2 |
| Manual edit → MANUAL_CONFIRMED | ✅ COMPLETE | 6B-2 |

### Phase 6B-2 Status: COMPLETE

#### Files Modified

| File | Changes |
|------|----------|
| `apps/paket_spm/views.py` | Wire adapter, manual edit handler, draft save |
| `apps/paket_spm/test_drpp_batch_flow.py` | 20 new web flow tests |
| `docs/CLAUDE_HANDOFF.md` | Updated status |

#### What Was Done

1. **Adapter Wired**: `build_dk_drafts_from_parsed_data()` called after parser creates parsed_data
2. **Preview 15 Columns**: Template already had 15-column table with correct order
3. **Manual Edit Handler**: `_update_dk_drafts_with_manual_edits()` updates field_source/field_status to MANUAL_CONFIRMED
4. **Draft REVIEW Save**: Draft with REVIEW status can be saved (recalculate action)
5. **Backward Compatibility**: Old parsed_data without dk_drafts still works

#### Key Behaviors

- **Generic GUP** → jenis_spm=null + REVIEW (NOT converted to GUP_REGULAR)
- **GUP_KKP** → no_drpp=null + NOT_APPLICABLE
- **Helper** is read-only (output tag, not input)
- **Null displays as empty**, not "-", "TANPA_DRPP", or implicit 0
- **Manual values preserved** on draft save
- **No TransactionDetail** created on draft save

#### Test Results

```
Ran 128 tests — OK (skipped=1)
TEST_EXIT: 0
CHECK_EXIT: 0
PYCOMPILE_EXIT: 0
DIFF_CHECK_EXIT: 0
```

### Phase 6B-3 Remains

1. Integrate ai_semantic_* with web upload
2. Final commit to TransactionDetail/D_K
3. Dashboard integration
4. Real PDF end-to-end testing

### Phase 6B Priorities

1. ~~Integrate ai_semantic_* with web upload~~
2. ~~Fix tanggal_spm (NOT from tgl_sp2d)~~
3. ~~Implement 15-column preview~~
4. ~~Fix KKP no_drpp = null~~
5. ~~Implement GUP_PNBP detection~~
6. ~~Add REVIEW status save~~
7. Validate FP as Decimal

### Evaluator Phase 5F Status: FROZEN

The evaluator is frozen. Do NOT modify evaluator logic unless contract tests prove a direct blocker.

- 117 tests passing
- Counterpart matching: strict receipt number only
- satker/year overlap does NOT match
- EXTRA canonical → ALWAYS FAIL

---

## Fase 5F: Evaluator Summary (FROZEN)

### Exit Codes

| Status | Meaning | Exit |
|--------|---------|------|
| PASS | All exact | 0 |
| REVIEW | Has unresolved/review items, no WRONG | 0 |
| FAIL | Has WRONG or hard failures | 1 |

### Hard Failures
- `canonical WRONG` field
- `EXTRA canonical transaction`
- `MISSING` without unresolved counterpart

### Counterpart Matching
- Only receipt NUMBER matches (e.g., "01011")
- satker/year overlap ignored
- Multiple candidates = ambiguous = no match

### Test Suite

```
Ran 117 tests in 0.690s — OK
TEST_EXIT: 0
CHECK_EXIT: 0
PYCOMPILE_EXIT: 0
DIFF_CHECK_EXIT: 0
```

---

## Contract Test Command

```bash
python manage.py test apps.core.test_dk_domain_contract -v 2
python manage.py check
python -m py_compile apps/core/dk_domain_contract.py
```
```
