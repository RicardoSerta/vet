import os
import re
import unicodedata
from django.contrib.auth.models import User
from .models import Profile
from datetime import date

from django import forms

from .models import Tutor, Clinic, Veterinarian, Pet


class ExamUploadForm(forms.Form):
    clinic_or_vet = forms.ChoiceField(
        label='Clínica / Veterinário',
        choices=[],
        widget=forms.Select()
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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        clinics = Clinic.objects.all().order_by('name')
        vets = Veterinarian.objects.all().order_by('name')

        clinic_choices = [(f"CLINIC:{c.id}", c.name) for c in clinics]
        vet_choices = [(f"VET:{v.id}", v.name) for v in vets]

        # Opção 1 (recomendada): dividir em grupos (Clínicas / Veterinários)
        self.fields['clinic_or_vet'].choices = [
            ('', 'Selecione...'),
            ('Clínicas', clinic_choices),
            ('Veterinários', vet_choices),
        ]
        
    def clean_clinic_or_vet(self):
        value = self.cleaned_data.get('clinic_or_vet')
        if not value:
            raise forms.ValidationError("Selecione uma clínica ou veterinário.")
        return value

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

class TutorForm(forms.ModelForm):
    class Meta:
        model = Tutor
        fields = ['name', 'email', 'phone']


class ClinicForm(forms.ModelForm):
    password = forms.CharField(
        label="Senha",
        required=True,
        min_length=6,
        widget=forms.PasswordInput(attrs={"placeholder": "Senha para login"})
    )

    class Meta:
        model = Clinic
        fields = ['name', 'email', 'phone', 'password']

    def save(self, commit=True):
        clinic = super().save(commit=False)

        base = _to_login_base(clinic.name)
        username = _make_unique_username(base)

        user = User(username=username)
        user.first_name = clinic.name  # para aparecer bonitinho no perfil/sidebar
        if clinic.email:
            user.email = clinic.email

        user.set_password(self.cleaned_data['password'])
        user.save()

        # cria/ajusta profile BASIC
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.role = 'BASIC'
        if clinic.phone and not profile.whatsapp:
            profile.whatsapp = clinic.phone
        profile.save()

        clinic.user = user
        if commit:
            clinic.save()

        # pra view mostrar a mensagem com o login
        self.created_username = username
        return clinic


class VeterinarianForm(forms.ModelForm):
    password = forms.CharField(
        label="Senha",
        required=True,
        min_length=6,
        widget=forms.PasswordInput(attrs={"placeholder": "Senha para login"})
    )

    class Meta:
        model = Veterinarian
        fields = ['name', 'email', 'phone', 'password']

    def save(self, commit=True):
        vet = super().save(commit=False)

        base = _to_login_base(vet.name)
        username = _make_unique_username(base)

        user = User(username=username)
        user.first_name = vet.name
        if vet.email:
            user.email = vet.email

        user.set_password(self.cleaned_data['password'])
        user.save()

        profile, _ = Profile.objects.get_or_create(user=user)
        profile.role = 'BASIC'
        if vet.phone and not profile.whatsapp:
            profile.whatsapp = vet.phone
        profile.save()

        vet.user = user
        if commit:
            vet.save()

        self.created_username = username
        return vet


class PetForm(forms.ModelForm):
    class Meta:
        model = Pet
        fields = ['name', 'breed', 'tutor']

    def clean_breed(self):
        breed = self.cleaned_data.get('breed', '').strip()
        if not breed:
            return 'SRD'
        return breed

def _to_login_base(name: str) -> str:
    name = (name or "").strip().lower()

    # remove acentos
    name = unicodedata.normalize("NFKD", name)
    name = "".join([c for c in name if not unicodedata.combining(c)])

    # troca espaços por ponto
    name = re.sub(r"\s+", ".", name)

    # deixa só caracteres permitidos no username do Django
    name = re.sub(r"[^a-z0-9@.+-_]", "", name)

    # evita vazio
    return name or "user"


def _make_unique_username(base: str) -> str:
    username = base
    i = 2
    while User.objects.filter(username=username).exists():
        username = f"{base}{i}"
        i += 1
    return username
