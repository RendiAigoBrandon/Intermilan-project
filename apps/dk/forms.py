from django import forms
from .models import TransactionDetail

class TransactionDetailForm(forms.ModelForm):
    class Meta:
        model = TransactionDetail
        fields = [
            'satker_code', 'akun', 'bulan_sp2d', 'cara_pembayaran',
            'nomor_spm', 'tanggal_spm', 'jenis_spm', 'no_kuitansi',
            'no_drpp', 'deskripsi', 'nilai_bruto', 'nilai_netto',
            'pembebanan', 'fp', 'pph21'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make the nominal fields essentially required in the form to avoid
        # empty values becoming None and crashing the db.
        self.fields['nilai_bruto'].required = True
        self.fields['nilai_netto'].required = True
        self.fields['pph21'].required = True

    def clean(self):
        cleaned_data = super().clean()
        
        # Temporary strategy for empty values on DecimalFields that don't allow null
        # Because we haven't done nullable migrations yet, if users submit empty
        # for these fields, we will block it with a validation error rather than
        # silently coercing them to 0.
        
        for field in ['nilai_bruto', 'nilai_netto', 'pph21']:
            if self.data.get(field) == '' or self.data.get(field) is None:
                self.add_error(field, "Kolom ini wajib diisi dengan angka (gunakan 0 jika memang nol). Kosong tidak didukung sebelum pembaruan sistem.")

        return cleaned_data
