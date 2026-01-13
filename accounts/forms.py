import os
import re
import unicodedata
from django.contrib.auth.models import User
from .models import Profile
from datetime import date
from datetime import datetime
from pathlib import Path

from django import forms

from .models import Tutor, Clinic, Veterinarian, Pet

def parse_exam_filename(filename: str):
    """
    Esperado: Laudo Pet Raça Tutor Exame DD.MM.YYYY.pdf
    Campos separados por espaço. Usa _ para representar espaços dentro de cada campo.
    """
    name = Path(filename).name

    if not name.lower().endswith(".pdf"):
        raise forms.ValidationError("O arquivo precisa ser um PDF (.pdf).")

    stem = name[:-4]  # remove .pdf
    parts = stem.split()

    if len(parts) != 6:
        raise forms.ValidationError(
            "Nome do arquivo inválido. Use: Laudo Pet Raça Tutor Exame DD.MM.YYYY.pdf"
        )

    if parts[0] != "Laudo":
        raise forms.ValidationError('O nome do arquivo deve começar com "Laudo".')

    pet_raw, breed_raw, tutor_raw, exam_raw, date_raw = parts[1:]

    pet = pet_raw.replace("_", " ").strip()
    breed = breed_raw.replace("_", " ").strip()
    tutor = tutor_raw.replace("_", " ").strip()
    exam_type = exam_raw.replace("_", " ").strip()

    try:
        date_realizacao = datetime.strptime(date_raw, "%d.%m.%Y").date()
    except ValueError:
        raise forms.ValidationError("Data inválida. Use DD.MM.YYYY (ex.: 05.03.2025).")

    return {
        "pet_name": pet,
        "breed": breed,
        "tutor_name": tutor,
        "exam_type": exam_type,
        "date_realizacao": date_realizacao,
    }

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def to_python(self, data):
        if not data:
            return []
        if isinstance(data, (list, tuple)):
            return list(data)
        return [data]

    def validate(self, data):
        if self.required and not data:
            raise forms.ValidationError("Selecione pelo menos um arquivo PDF.")



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
        cleaned_data = super().clean()
        pdf = cleaned_data.get("pdf_file")

        if not pdf:
            return cleaned_data

        # pega só o nome do arquivo (sem caminho)
        filename = Path(pdf.name).name

        # valida extensão
        if not filename.lower().endswith(".pdf"):
            raise forms.ValidationError("O arquivo precisa ser um PDF (.pdf).")

        stem = filename[:-4]  # remove ".pdf"

        # quebra por espaços (se tiver múltiplos espaços, split() resolve)
        parts = stem.split()

        # Esperado:
        # Laudo Pet Raça Tutor Exame DD.MM.YYYY
        if len(parts) != 6:
            raise forms.ValidationError(
                "Nome do arquivo inválido. Use o formato: "
                "Laudo Pet Raça Tutor Exame DD.MM.YYYY.pdf"
            )

        if parts[0] != "Laudo":
            raise forms.ValidationError(
                'Nome do arquivo inválido: ele deve começar com "Laudo".'
            )

        pet_raw, breed_raw, tutor_raw, exam_raw, date_raw = parts[1:]

        # underscores viram espaços
        pet = pet_raw.replace("_", " ").strip()
        breed = breed_raw.replace("_", " ").strip()
        tutor = tutor_raw.replace("_", " ").strip()
        exam_type = exam_raw.replace("_", " ").strip()

        # data no formato DD.MM.YYYY
        try:
            date_realizacao = datetime.strptime(date_raw, "%d.%m.%Y").date()
        except ValueError:
            raise forms.ValidationError(
                "Data inválida no nome do arquivo. Use DD.MM.YYYY (ex.: 12.01.2026)."
            )

        # salva nos campos “parsed_...” que sua view já usa
        cleaned_data["parsed_pet_name"] = pet
        cleaned_data["parsed_breed"] = breed
        cleaned_data["parsed_tutor_name"] = tutor
        cleaned_data["parsed_exam_type"] = exam_type
        cleaned_data["parsed_date_realizacao"] = date_realizacao

        return cleaned_data


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
    
class MultiExamUploadForm(forms.Form):
    clinic_or_vet = forms.ChoiceField(
        label='Clínica / Veterinário',
        choices=[],
        widget=forms.Select()
    )

    pdf_files = MultipleFileField(
        label="Arquivos PDF",
        required=True,
        widget=MultipleFileInput(attrs={"multiple": True})
    )

    MAX_FILES = 20  # pode mudar para 50 se quiser

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        clinics = Clinic.objects.all().order_by('name')
        vets = Veterinarian.objects.all().order_by('name')

        clinic_choices = [(f"CLINIC:{c.id}", c.name) for c in clinics]
        vet_choices = [(f"VET:{v.id}", v.name) for v in vets]

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

    def clean_pdf_files(self):
        files = self.cleaned_data.get("pdf_files", [])
        if len(files) > self.MAX_FILES:
            raise forms.ValidationError(f"Você pode enviar no máximo {self.MAX_FILES} PDFs por vez.")

        # valida cada nome
        for f in files:
            parse_exam_filename(f.name)

        return files
