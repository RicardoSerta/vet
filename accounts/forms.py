import os
import re
from datetime import date

from django import forms


class ExamUploadForm(forms.Form):
    clinic_or_vet = forms.CharField(
        label='Clínica / Veterinário',
        max_length=255,
        widget=forms.TextInput(attrs={
            'placeholder': 'Nome da clínica ou veterinário',
        })
    )

    tutor_phone = forms.CharField(
        label='Celular do tutor',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': '(xx) xxxx-xxxx',
        })
    )

    tutor_email = forms.CharField(
        label='E-mail do tutor',
        max_length=255,
        required=False,
        widget=forms.EmailInput(attrs={
            'placeholder': 'email@exemplo.com',
        })
    )

    observations = forms.CharField(
        label='Observações',
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
        })
    )

    pdf_file = forms.FileField(
        label='Arquivo PDF',
        widget=forms.ClearableFileInput(attrs={'accept': 'application/pdf'})
    )

    def clean_tutor_phone(self):
        phone = self.cleaned_data.get('tutor_phone', '').strip()
        if phone:
            pattern = r'^\(\d{2}\)\s?\d{4,5}-\d{4}$'
            if not re.match(pattern, phone):
                raise forms.ValidationError(
                    'Use o formato (xx) xxxx-xxxx ou (xx) 9xxxx-xxxx.'
                )
        return phone

    def clean(self):
        cleaned = super().clean()
        pdf = cleaned.get('pdf_file')

        if not pdf:
            return cleaned

        name = os.path.basename(pdf.name)

        if not name.lower().endswith('.pdf'):
            self.add_error('pdf_file', 'O arquivo deve ser um PDF (.pdf).')
            return cleaned

        base = name[:-4]  # remove .pdf
        parts = base.split('-')

        # Esperamos: Pet - Tutor - Raça - Exame - YYYY - MM - DD  (7 partes)
        if len(parts) != 7:
            self.add_error(
                'pdf_file',
                'O nome do arquivo deve estar no formato '
                'Pet-Tutor-Raça-Exame-YYYY-MM-DD.pdf',
            )
            return cleaned

        pet, tutor, breed, exam_type, year, month, day = parts

        try:
            data_realizacao = date(int(year), int(month), int(day))
        except ValueError:
            self.add_error('pdf_file', 'Data inválida no nome do arquivo.')
            return cleaned

        # Guardamos os dados "escondidos" aqui, pra view usar ao salvar
        cleaned['parsed_pet_name'] = pet
        cleaned['parsed_tutor_name'] = tutor
        cleaned['parsed_breed'] = breed
        cleaned['parsed_exam_type'] = exam_type
        cleaned['parsed_date_realizacao'] = data_realizacao

        return cleaned

