import os
import re
import unicodedata
from django.contrib.auth.models import User
from .models import Profile
from datetime import date
from datetime import datetime
from pathlib import Path
from django.core.exceptions import ValidationError
from django import forms

from .models import Tutor, Clinic, Veterinarian, Pet, Profile, ExamTypeAlias

ALLOWED_EXTRA_EXTENSIONS = {".pdf", ".avi", ".png", ".jpg", ".jpeg"}
MAX_EXTRA_FILES = 5

class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    Aceita 0..N arquivos. Retorna uma lista de UploadedFile.
    """
    def clean(self, data, initial=None):
        if not data:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]

        cleaned_files = []
        for f in data:
            cleaned_files.append(super().clean(f, initial))
        return cleaned_files

def parse_exam_filename(filename: str):
    """
    Esperado: Laudo Pet Raça Tutor Exame DD.MM.YYYY.pdf
    Campos separados por espaço. Usa _ para representar espaços dentro de cada campo.
    """
    name = Path(filename).name

    if not name.lower().endswith(".pdf"):
        raise forms.ValidationError("O arquivo precisa ser um PDF.")

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
        raise forms.ValidationError("Data inválida. Use DD.MM.YYYY (ex.: 31.12.2025).")

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
    notify_tutor_phone = forms.CharField(required=False, initial="1", widget=forms.HiddenInput())
    notify_tutor_email = forms.CharField(required=False, initial="1", widget=forms.HiddenInput())
    
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
            'placeholder': '(XX) XXXX-XXXX',
            # HTML5: só valida se tiver algo preenchido
            'pattern': r'^\(\d{2}\)\s?(\d{4}-\d{4}|9\d{4}-\d{4})$',
            'title': 'Use (XX) XXXX-XXXX ou (XX) 9XXXX-XXXX',
        })
    )

    tutor_email = forms.EmailField(
        label='E-mail do tutor',
        max_length=255,
        required=False,
        error_messages={
            "invalid": "Informe um e-mail válido (ex: nome@email.com)."
        },
        widget=forms.EmailInput(attrs={
            "placeholder": "exemplo@email.com",
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
        required=True,
        widget=forms.ClearableFileInput(attrs={
            "accept": "application/pdf",
            "class": "file-input-hidden",
        }),
        label="Arquivo PDF",
    )
    
    additional_clinic_or_vet = forms.MultipleChoiceField(
        label="Adicionar Clínicas/Veterinários",
        required=False,
        choices=[],
        widget=forms.SelectMultiple(attrs={
            "size": 6,   # altura do select quando abrir
        })
    )
    
    extra_files = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={
            "multiple": True,
            "accept": ".pdf,.avi,.png,.jpg,.jpeg",
            "class": "file-input-hidden",
        }),
        label="Arquivos extras",
    )


    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        clinics = Clinic.objects.all().order_by('name')
        vets = Veterinarian.objects.all().order_by('name')

        clinic_choices = [(f"CLINIC:{c.id}", c.display_name) for c in clinics]
        vet_choices = [(f"VET:{v.id}", v.display_name) for v in vets]
        
        self.fields['clinic_or_vet'].choices = [
            ('', 'Selecione...'),
            ('Clínicas', clinic_choices),
            ('Veterinários', vet_choices),
        ]

        # No múltiplo
        self.fields['additional_clinic_or_vet'].choices = [
            ('Clínicas', clinic_choices),
            ('Veterinários', vet_choices),
        ]
        
    def clean_clinic_or_vet(self):
        value = self.cleaned_data.get('clinic_or_vet')
        if not value:
            raise forms.ValidationError("Selecione uma clínica ou veterinário.")
        return value
        
    def clean_additional_clinic_or_vet(self):
        items = self.cleaned_data.get("additional_clinic_or_vet") or []
        if len(items) > 2:
            raise forms.ValidationError("Selecione no máximo 2 clínicas/veterinários.")
        return items

    def clean_tutor_phone(self):
        phone = self.cleaned_data.get('tutor_phone', '').strip()
        if phone:
            pattern = r'^\(\d{2}\)\s?(\d{4}-\d{4}|9\d{4}-\d{4})$'
            if not re.match(pattern, phone):
                raise forms.ValidationError("Use o formato (XX) XXXX-XXXX ou (XX) 9XXXX-XXXX.")
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
            raise forms.ValidationError("O arquivo precisa ser um PDF.")

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
                "Data inválida no nome do arquivo. Use DD.MM.YYYY (ex.: 31.12.2025)."
            )

        cleaned_data["parsed_pet_name"] = pet
        cleaned_data["parsed_breed"] = breed
        cleaned_data["parsed_tutor_name"] = tutor
        cleaned_data["parsed_exam_type"] = exam_type
        cleaned_data["parsed_date_realizacao"] = date_realizacao
        
        main = (cleaned_data.get("clinic_or_vet") or "").strip()
        extras = cleaned_data.get("additional_clinic_or_vet") or []

        # remove do extras se for igual ao principal
        if main:
            extras = [x for x in extras if x != main]

        # mantém no máximo 2 (garantia backend)
        extras = extras[:2]

        cleaned_data["additional_clinic_or_vet"] = extras

        return cleaned_data
        
    def clean_extra_files(self):
        files = self.cleaned_data.get("extra_files") or []

        if len(files) > 5:
            raise forms.ValidationError("Você pode anexar no máximo 5 arquivos extras.")

        allowed_exts = {".pdf", ".avi", ".png", ".jpg", ".jpeg"}

        for f in files:
            ext = Path(f.name).suffix.lower()
            if ext not in allowed_exts:
                raise forms.ValidationError(
                    "Formato inválido nos arquivos extras. Use apenas: PDF, AVI, PNG, JPG ou JPEG."
                )

        return files

class TutorForm(forms.ModelForm):
    class Meta:
        model = Tutor
        fields = ["name", "surname", "email", "phone"]

        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Nome do tutor"}),
            "surname": forms.TextInput(attrs={"placeholder": "Sobrenome do tutor"}),
            "email": forms.EmailInput(attrs={"placeholder": "exemplo@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "(XX) XXXX-XXXX"}),
        }
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do tutor'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'

class ClinicForm(forms.ModelForm):
    class Meta:
        model = Clinic
        fields = ["name", "email", "phone"]

        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Nome da clínica"}),
            "email": forms.EmailInput(attrs={"placeholder": "exemplo@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "(XX) XXXX-XXXX"}),
        }

    def save(self, commit=True):
        clinic = super().save(commit=False)

        # flags/mensagens para a view (mantemos para não quebrar o views.py)
        self.created_username = None
        self.updated_username = None
        self.password_changed = False

        existing_user = getattr(self.instance, "user", None)

        # Se já existe usuário vinculado, atualiza dados básicos (mas NÃO mexe em senha)
        if existing_user:
            base = _to_login_base(clinic.name)
            new_username = _make_unique_username(base, exclude_user_id=existing_user.id)

            if existing_user.username != new_username:
                existing_user.username = new_username
                self.updated_username = new_username

            existing_user.first_name = clinic.name
            existing_user.email = clinic.email or ""
            existing_user.save()

            profile, _ = Profile.objects.get_or_create(user=existing_user)
            profile.role = 'BASIC'
            profile.whatsapp = clinic.phone or ""
            profile.save()

            clinic.user = existing_user

            if commit:
                clinic.save()

            # mantém consistência da coluna exibida na tabela de exames
            from .models import Exam
            Exam.objects.filter(assigned_user=existing_user).update(clinic_or_vet=clinic.name)

            return clinic

        # Se NÃO existe user, agora só salva a clínica (sem criar login e sem senha)
        if commit:
            clinic.save()
        return clinic

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome da clínica'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'


class VeterinarianForm(forms.ModelForm):
    class Meta:
        model = Veterinarian
        fields = ["name", "surname", "email", "phone"]

        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Nome do Veterinário"}),
            "surname": forms.TextInput(attrs={"placeholder": "Sobrenome do veterinário"}),
            "email": forms.EmailInput(attrs={"placeholder": "exemplo@email.com"}),
            "phone": forms.TextInput(attrs={"placeholder": "(XX) XXXX-XXXX"}),
        }

    def save(self, commit=True):
        vet = super().save(commit=False)

        # flags/mensagens para a view (mantemos para não quebrar o views.py)
        self.created_username = None
        self.updated_username = None
        self.password_changed = False

        existing_user = getattr(self.instance, "user", None)

        # Se já existe usuário vinculado, atualiza dados básicos (mas NÃO mexe em senha)
        if existing_user:
            base = _to_login_base(vet.name)
            new_username = _make_unique_username(base, exclude_user_id=existing_user.id)

            if existing_user.username != new_username:
                existing_user.username = new_username
                self.updated_username = new_username

            existing_user.first_name = vet.name
            existing_user.email = vet.email or ""
            existing_user.save()

            profile, _ = Profile.objects.get_or_create(user=existing_user)
            profile.role = 'BASIC'
            profile.whatsapp = vet.phone or ""
            profile.save()

            vet.user = existing_user

            if commit:
                vet.save()

            # Atualiza a coluna exibida na tabela de exames
            from .models import Exam
            Exam.objects.filter(assigned_user=existing_user).update(clinic_or_vet=vet.name)

            return vet

        # Se NÃO existe user, agora só salva o veterinário (sem criar login e sem senha)
        if commit:
            vet.save()
        return vet

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do veterinário'})
        self.fields['surname'].widget.attrs.update({'placeholder': 'Sobrenome do veterinário'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'


class PetForm(forms.ModelForm):
    class Meta:
        model = Pet
        fields = ['name', 'breed', 'tutor']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do pet'})
        self.fields['breed'].widget.attrs.update({'placeholder': 'Raça do pet'})

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


def _make_unique_username(base: str, exclude_user_id=None) -> str:
    username = base
    i = 2
    while True:
        qs = User.objects.filter(username=username)
        if exclude_user_id is not None:
            qs = qs.exclude(id=exclude_user_id)
        if not qs.exists():
            return username
        username = f"{base}{i}"
        i += 1
    
class MultiExamUploadForm(forms.Form):
    clinic_or_vet = forms.ChoiceField(
        label='Clínica / Veterinário',
        choices=[],
        widget=forms.Select()
    )

    pdf_files = MultipleFileField(
        required=True,
        widget=MultipleFileInput(attrs={
            "multiple": True,
            "accept": "application/pdf",
            "class": "file-input-hidden",
        }),
        label="Arquivos PDF",
    )


    MAX_FILES = 50

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        clinics = Clinic.objects.all().order_by('name')
        vets = Veterinarian.objects.all().order_by('name')

        clinic_choices = [(f"CLINIC:{c.id}", c.display_name) for c in clinics]
        vet_choices = [(f"VET:{v.id}", v.display_name) for v in vets]

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
        
class ExamTypeAliasForm(forms.ModelForm):
    class Meta:
        model = ExamTypeAlias
        fields = ["abbreviation", "full_name"]
        widgets = {
            "abbreviation": forms.TextInput(attrs={"placeholder": "Sigla (ex: eco, egc, rx)"}),
            "full_name": forms.TextInput(attrs={"placeholder": "Nome real do exame (ex: Ecocardiograma)"}),
        }

    def clean_abbreviation(self):
        abbr = (self.cleaned_data.get("abbreviation") or "").strip().lower()
        if not abbr:
            raise forms.ValidationError("Informe a sigla.")
        return abbr

    def clean_full_name(self):
        name = (self.cleaned_data.get("full_name") or "").strip()
        if not name:
            raise forms.ValidationError("Informe o nome real do exame.")
        return name
        
class AdminAuxForm(forms.Form):
    first_name = forms.CharField(
        label="NAME",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Nome do auxiliar"}),
    )
    last_name = forms.CharField(
        label="SOBRENOME",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Sobrenome do auxiliar"}),
    )
    email = forms.EmailField(
        label="EMAIL",
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "exemplo@email.com"}),
    )
    phone = forms.CharField(
        label="PHONE",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "(XX) XXXX-XXXX"}),
    )

    def clean_phone(self):
        # (deixa simples por enquanto)
        return (self.cleaned_data.get("phone") or "").strip()

