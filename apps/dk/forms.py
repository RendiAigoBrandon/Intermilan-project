from django import forms
from .models import TransactionDetail, MasterAkun
from apps.accounts.access import can_view_all_satker, get_user_satker_code, get_profile

BULAN_CHOICES = [
    ('', '--- Pilih Bulan ---'),
    ('1', 'Januari'), ('2', 'Februari'), ('3', 'Maret'), ('4', 'April'),
    ('5', 'Mei'), ('6', 'Juni'), ('7', 'Juli'), ('8', 'Agustus'),
    ('9', 'September'), ('10', 'Oktober'), ('11', 'November'), ('12', 'Desember'),
]

CARA_PEMBAYARAN_CHOICES = [
    ('', '--- Pilih Cara Pembayaran ---'),
    ('UP/TUP', 'UP/TUP'),
    ('LS', 'LS'),
    ('LS Kontraktual', 'LS Kontraktual'),
    ('LS Non Kontraktual', 'LS Non Kontraktual'),
]

class TransactionDetailForm(forms.ModelForm):
    bulan_sp2d = forms.ChoiceField(choices=BULAN_CHOICES, required=False, label="SP2D Bulan")
    cara_pembayaran = forms.ChoiceField(choices=CARA_PEMBAYARAN_CHOICES, required=False, label="Cara Pembayaran")
    tanggal_spm = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="Tanggal SPM")

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
        
        # Populate akun choices
        active_akuns = MasterAkun.objects.filter(is_active=True)
        self.fields['akun'].widget = forms.Select(choices=[('', '--- Pilih Akun ---')] + [(a.kode, f"{a.kode} - {a.nama_akun}") for a in active_akuns])
        
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
            self.fields['satker_code'].widget.attrs['readonly'] = True
            user_satker_code = get_user_satker_code(self.user)
            if user_satker_code:
                self.fields['satker_code'].initial = user_satker_code
                self.fields['satker_code'].widget = forms.Select(choices=[(user_satker_code, user_satker_code)])
            else:
                self.fields['satker_code'].widget.attrs['disabled'] = True

        self.fields['nilai_bruto'].required = True
        self.fields['nilai_netto'].required = True
        self.fields['pph21'].required = True

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
