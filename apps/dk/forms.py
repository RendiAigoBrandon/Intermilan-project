import re

from django import forms
from .models import TransactionDetail, MasterAkun
from apps.accounts.access import can_view_all_satker, filter_by_satker, get_user_satker_code
from apps.core.satker import get_satker_name_map
from apps.sp2d.models import SP2DRaw


AKUN_PATTERN = re.compile(r"^[0-9][0-9A-Za-z./_-]{1,31}$")

BULAN_CHOICES = [
    ('', '--- Opsional / Pilih Bulan ---'),
    ('1', 'Januari'), ('2', 'Februari'), ('3', 'Maret'), ('4', 'April'),
    ('5', 'Mei'), ('6', 'Juni'), ('7', 'Juli'), ('8', 'Agustus'),
    ('9', 'September'), ('10', 'Oktober'), ('11', 'November'), ('12', 'Desember'),
]


def coerce_optional_int(value):
    if value in (None, ""):
        return None
    return int(value)

CARA_PEMBAYARAN_CHOICES = [
    ('', '--- Opsional / Pilih Cara Pembayaran ---'),
    ('UP/TUP', 'UP/TUP'),
    ('LS', 'LS'),
    ('LS Kontraktual', 'LS Kontraktual'),
    ('LS Non Kontraktual', 'LS Non Kontraktual'),
]

class TransactionDetailForm(forms.ModelForm):
    satker_code = forms.ChoiceField(choices=[('', '--- Pilih Satker ---')], label="Satker")
    akun = forms.CharField(
        max_length=32,
        label="Akun",
        widget=forms.TextInput(attrs={
            "list": "dk-akun-suggestions",
            "placeholder": "Ketik atau pilih akun",
            "autocomplete": "off",
        }),
    )
    bulan_sp2d = forms.TypedChoiceField(
        choices=BULAN_CHOICES,
        coerce=coerce_optional_int,
        empty_value=None,
        required=False,
        label="Bulan SP2D",
    )
    cara_pembayaran = forms.ChoiceField(choices=CARA_PEMBAYARAN_CHOICES, required=False, label="Cara Pembayaran")
    tanggal_spm = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Tanggal SPM")
    sp2d_raw_id = forms.ChoiceField(choices=[('', '--- Opsional / Tanpa SP2D ---')], required=False, label="No SP2D")

    class Meta:
        model = TransactionDetail
        fields = [
            'satker_code', 'akun', 'bulan_sp2d', 'cara_pembayaran',
            'nomor_spm', 'tanggal_spm', 'jenis_spm', 'no_kuitansi',
            'no_drpp', 'deskripsi', 'nilai_bruto', 'nilai_netto',
            'pembebanan', 'fp', 'pph21'
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.allowed_satker_codes = set()
        self.fields['satker_code'].label = "Satker"
        self.fields['satker_code'].required = True
        self.fields['akun'].label = "Akun"
        self.fields['nomor_spm'].label = "Nomor SPM"
        self.fields['tanggal_spm'].label = "Tanggal SPM (Opsional)"
        self.fields['jenis_spm'].label = "Jenis SPM"
        self.fields['no_kuitansi'].label = "No. Kuitansi"
        self.fields['no_drpp'].label = "No. DRPP"
        self.fields['deskripsi'].label = "Deskripsi"
        self.fields['nilai_bruto'].label = "Nilai Bruto"
        self.fields['nilai_netto'].label = "Nilai Netto"
        self.fields['pembebanan'].label = "Pembebanan"
        self.fields['fp'].label = "FP"
        self.fields['pph21'].label = "PPh21"
        for field_name in ["nomor_spm", "jenis_spm", "no_kuitansi", "no_drpp", "deskripsi", "pembebanan"]:
            self.fields[field_name].widget.attrs.setdefault("placeholder", "Opsional - isi jika tersedia")
        self.fields["fp"].widget.attrs.setdefault("placeholder", "Opsional")
        sp2d_qs = SP2DRaw.objects.all()
        tx_qs = TransactionDetail.objects.exclude(satker_code="")
        if self.user:
            sp2d_qs = filter_by_satker(sp2d_qs, self.user)
            tx_qs = filter_by_satker(tx_qs, self.user)

        if self.instance and self.instance.pk:
            selected_sp2d_id = str(getattr(self.instance, "sp2d_raw_id", "") or "")
        else:
            selected_sp2d_id = str(
                self.data.get("sp2d_raw_id")
                or self.initial.get("sp2d_raw_id")
                or ""
            )
        current_satker = (
            self.data.get("satker_code")
            if self.is_bound
            else self.initial.get("satker_code") or getattr(self.instance, "satker_code", "")
        )

        sp2d_satkers = set(
            sp2d_qs.exclude(satker_code="")
            .values_list("satker_code", flat=True)
            .distinct()
        )
        tx_satkers = set(
            tx_qs.values_list("satker_code", flat=True)
            .distinct()
        )
        if self.instance and self.instance.pk and self.instance.satker_code:
            tx_satkers.add(self.instance.satker_code)
        if self.initial.get("satker_code"):
            tx_satkers.add(self.initial["satker_code"])
        if self.user and not can_view_all_satker(self.user):
            user_satker_code = get_user_satker_code(self.user)
            if user_satker_code:
                tx_satkers.add(user_satker_code)
                current_satker = user_satker_code
        elif current_satker and current_satker in (sp2d_satkers | tx_satkers):
            tx_satkers.add(current_satker)

        known_satkers = sp2d_satkers | tx_satkers
        names = get_satker_name_map(known_satkers)
        satker_choices = [('', '--- Pilih Satker ---')] + [
            (code, f"{code} - {names.get(code, '')}".rstrip(" -"))
            for code in sorted(known_satkers)
            if code
        ]
        self.fields['satker_code'].choices = satker_choices
        self.allowed_satker_codes = {code for code, _label in satker_choices if code}
        self.akun_suggestions = self._akun_suggestions(tx_qs)

        self.sp2d_rows = list(sp2d_qs.order_by("satker_code", "-created_at", "no_sp2d")[:1000])
        sp2d_choices = [('', '--- Opsional / Tanpa SP2D ---')]
        self.sp2d_json = []
        for row in self.sp2d_rows:
            label = " | ".join(
                part for part in [
                    row.no_sp2d,
                    row.satker_code,
                    row.satker_name,
                    f"SPM {row.nomor_spm_extracted}" if row.nomor_spm_extracted else "",
                ]
                if part
            )
            sp2d_choices.append((str(row.id), label or f"SP2D #{row.id}"))
            self.sp2d_json.append({
                "id": str(row.id),
                "satker_code": row.satker_code or "",
                "satker_name": row.satker_name or names.get(row.satker_code, ""),
                "no_sp2d": row.no_sp2d or "",
            })
        if selected_sp2d_id and selected_sp2d_id not in {choice[0] for choice in sp2d_choices}:
            linked = sp2d_qs.filter(id=int(selected_sp2d_id)).first() if selected_sp2d_id.isdigit() else None
            if linked:
                label = " | ".join(part for part in [linked.no_sp2d, linked.satker_code, linked.satker_name] if part)
                sp2d_choices.append((str(linked.id), label or f"SP2D #{linked.id}"))
                self.sp2d_json.append({
                    "id": str(linked.id),
                    "satker_code": linked.satker_code or "",
                    "satker_name": linked.satker_name or names.get(linked.satker_code, ""),
                    "no_sp2d": linked.no_sp2d or "",
                })
        self.fields['sp2d_raw_id'].choices = sp2d_choices
        if selected_sp2d_id:
            self.fields['sp2d_raw_id'].initial = selected_sp2d_id
        if self.instance and self.instance.pk:
            self.fields['sp2d_raw_id'].disabled = True

        self.satker_json = [
            {"code": code, "name": names.get(code, "")}
            for code, _label in satker_choices
            if code
        ]
        self.satker_name = names.get(current_satker, "")
        if not self.satker_name and selected_sp2d_id:
            self.satker_name = next((row["satker_name"] for row in self.sp2d_json if row["id"] == selected_sp2d_id), "")
        self.helper_value = f"{self._form_value('akun') or ''}{self._form_value('no_kuitansi') or ''}"
        
        # Add existing cara_pembayaran to choices if not in list
        existing_cp = None
        if self.instance and self.instance.pk:
            existing_cp = self.instance.cara_pembayaran
        elif 'cara_pembayaran' in self.data:
            existing_cp = self.data.get('cara_pembayaran')
            
        if existing_cp:
            current_choices = [c[0] for c in CARA_PEMBAYARAN_CHOICES]
            if existing_cp not in current_choices:
                self.fields['cara_pembayaran'].choices = CARA_PEMBAYARAN_CHOICES + [(existing_cp, existing_cp)]

        # Lock satker if not admin
        if self.user and not can_view_all_satker(self.user):
            user_satker_code = get_user_satker_code(self.user)
            if user_satker_code:
                self.fields['satker_code'].initial = user_satker_code
                self.fields['satker_code'].choices = [
                    (user_satker_code, f"{user_satker_code} - {names.get(user_satker_code, '')}".rstrip(" -"))
                ]
                self.allowed_satker_codes = {user_satker_code}
                self.fields['satker_code'].disabled = True
                self.satker_name = names.get(user_satker_code, self.satker_name)
            else:
                self.fields['satker_code'].disabled = True

        self.fields['nilai_bruto'].required = True
        self.fields['nilai_netto'].required = True
        self.fields['pph21'].required = True

    def _akun_suggestions(self, tx_qs):
        suggestions = []
        seen = set()
        master_rows = MasterAkun.objects.filter(is_active=True).values_list("kode", "nama_akun")
        for kode, nama_akun in master_rows:
            kode = (kode or "").strip()
            if self._valid_akun_format(kode) and kode not in seen:
                suggestions.append({"kode": kode, "label": f"{kode} - {nama_akun}".rstrip(" -")})
                seen.add(kode)

        akun_rows = (
            tx_qs.exclude(akun="")
            .values_list("akun", flat=True)
            .distinct()
            .order_by("akun")
        )
        for akun in akun_rows:
            akun = (akun or "").strip()
            if self._valid_akun_format(akun) and akun not in seen:
                suggestions.append({"kode": akun, "label": akun})
                seen.add(akun)

        existing_akun = getattr(self.instance, "akun", "") if self.instance and self.instance.pk else ""
        existing_akun = (existing_akun or "").strip()
        if self._valid_akun_format(existing_akun) and existing_akun not in seen:
            suggestions.append({"kode": existing_akun, "label": existing_akun})

        return suggestions

    def _valid_akun_format(self, value):
        return bool(AKUN_PATTERN.fullmatch((value or "").strip()))

    def _form_value(self, field_name):
        if self.is_bound:
            return self.data.get(field_name, "")
        value = self.initial.get(field_name)
        if value not in (None, ""):
            return value
        return getattr(self.instance, field_name, "")

    def clean_akun(self):
        akun = (self.cleaned_data.get("akun") or "").strip()
        if akun and not self._valid_akun_format(akun):
            raise forms.ValidationError(
                "Akun harus berupa kode tanpa spasi, diawali angka, maksimal 32 karakter."
            )
        return akun

    def clean(self):
        cleaned_data = super().clean()
        for field in ['nilai_bruto', 'nilai_netto', 'pph21']:
            if self.data.get(field) == '' or self.data.get(field) is None:
                self.add_error(field, "Isi 0 hanya jika nilai dokumen memang nol. Kosong tidak didukung.")
                
        # Force satker_code to user's satker if not allowed to change
        if self.user and not can_view_all_satker(self.user):
            user_satker_code = get_user_satker_code(self.user)
            if user_satker_code:
                cleaned_data['satker_code'] = user_satker_code
        elif cleaned_data.get("satker_code") and cleaned_data["satker_code"] not in self.allowed_satker_codes:
            self.add_error("satker_code", "Satker tidak ditemukan dalam data yang tersedia.")

        sp2d_raw_id = cleaned_data.get("sp2d_raw_id")
        posted_sp2d_raw_id = self.data.get("sp2d_raw_id") if self.is_bound else sp2d_raw_id
        if posted_sp2d_raw_id and not sp2d_raw_id:
            self.add_error(None, "SP2D tidak ditemukan atau beda satker.")
        elif sp2d_raw_id:
            sp2d_qs = SP2DRaw.objects.all()
            if self.user:
                sp2d_qs = filter_by_satker(sp2d_qs, self.user)
            sp2d = sp2d_qs.filter(id=sp2d_raw_id).first()
            if not sp2d:
                self.add_error("sp2d_raw_id", "SP2D tidak ditemukan atau tidak sesuai akses.")
                self.add_error(None, "SP2D tidak ditemukan atau beda satker.")
            elif sp2d.satker_code != cleaned_data.get("satker_code"):
                self.add_error("sp2d_raw_id", "No SP2D harus sesuai Satker.")

        return cleaned_data


class TransactionBulkEditForm(forms.Form):
    bulan_sp2d = forms.ChoiceField(choices=BULAN_CHOICES, required=False, label="SP2D Bulan (Biarkan kosong jika tidak diubah)")
    cara_pembayaran = forms.ChoiceField(choices=CARA_PEMBAYARAN_CHOICES, required=False, label="Cara Pembayaran (Biarkan kosong jika tidak diubah)")
    jenis_spm = forms.CharField(max_length=50, required=False, label="Jenis SPM (Biarkan kosong jika tidak diubah)")
    
    # Status Detail (excluding DIARSIPKAN)
    STATUS_CHOICES = [('', '--- Pilih Status (Biarkan kosong jika tidak diubah) ---')] + [
        (c.value, c.label) for c in TransactionDetail.StatusDetail if c.value != TransactionDetail.StatusDetail.DIARSIPKAN
    ]
    status_detail = forms.ChoiceField(choices=STATUS_CHOICES, required=False, label="Status Dokumen")
