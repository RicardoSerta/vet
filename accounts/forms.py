import os
import re
import unicodedata
from django.contrib.auth.models import User
from .models import Profile
from datetime import date
from datetime import datetime
from pathlib import Path

from django import forms

from .models import Tutor, Clinic, Veterinarian, Pet, Profile, ExamTypeAlias

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
            'placeholder': '(XX) XXXX-XXXX',
        })
    )

    tutor_email = forms.CharField(
        label='E-mail do tutor',
        max_length=255,
        required=False,
        widget=forms.EmailInput(attrs={
            'placeholder': 'exemplo@email.com',
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
    
    extra_pdfs = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={
            "multiple": True,
            "accept": "application/pdf",
            "class": "file-input-hidden",
        }),
        label="PDFs extras",
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
                    'Use o formato (XX) XXXX-XXXX ou (xx) 9XXXX-XXXXX.'
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
        
    def clean_extra_pdfs(self):
        files = self.cleaned_data.get("extra_pdfs", [])

        if len(files) > 5:
            raise forms.ValidationError("Você pode anexar no máximo 5 PDFs extras.")

        for f in files:
            if not f.name.lower().endswith(".pdf"):
                raise forms.ValidationError("Apenas arquivos PDF são aceitos nos PDFs extras.")

        return files

class TutorForm(forms.ModelForm):
    class Meta:
        model = Tutor
        fields = ['name', 'email', 'phone']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do Tutor'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'


class ClinicForm(forms.ModelForm):
    password = forms.CharField(
        label="Senha",
        required=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Senha para login"})
    )

    class Meta:
        model = Clinic
        fields = ['name', 'email', 'phone', 'password']
        
    def clean_password(self):
        pwd = self.cleaned_data.get("password") or ""
        pwd = pwd.strip()
        if pwd and len(pwd) < 6:
            raise forms.ValidationError("A senha deve ter pelo menos 6 caracteres.")
        return pwd

    def save(self, commit=True):
        clinic = super().save(commit=False)
        pwd = (self.cleaned_data.get("password") or "").strip()

        self.created_username = None
        self.updated_username = None
        self.password_changed = False

        existing_user = getattr(self.instance, "user", None)

        # Se já existe user, sempre manter username alinhado com o nome (se mudou)
        if existing_user:
            base = _to_login_base(clinic.name)
            new_username = _make_unique_username(base, exclude_user_id=existing_user.id)

            if existing_user.username != new_username:
                existing_user.username = new_username
                self.updated_username = new_username

            existing_user.first_name = clinic.name
            if clinic.email:
                existing_user.email = clinic.email or ""
            existing_user.save()

            profile, _ = Profile.objects.get_or_create(user=existing_user)
            profile.role = 'BASIC'
            if clinic.phone:
                profile.whatsapp = clinic.phone or ""
            profile.save()

            # Se senha foi preenchida, atualiza senha
            if pwd:
                existing_user.set_password(pwd)
                existing_user.save()
                self.password_changed = True

            clinic.user = existing_user

            if commit:
                clinic.save()

            # mantém consistência da coluna na tabela de exames
            from .models import Exam
            Exam.objects.filter(assigned_user=existing_user).update(clinic_or_vet=clinic.name)

            return clinic

        # Se NÃO existe user:
        # - Se não digitou senha: salva só a clínica, sem criar usuário
        if not pwd:
            if commit:
                clinic.save()
            return clinic

        # - Se digitou senha: cria user e vincula
        base = _to_login_base(clinic.name)
        username = _make_unique_username(base)

        user = User(username=username)
        user.first_name = clinic.name
        if clinic.email:
            user.email = clinic.email
        user.set_password(pwd)
        user.save()

        profile, _ = Profile.objects.get_or_create(user=user)
        profile.role = 'BASIC'
        if clinic.phone:
            profile.whatsapp = clinic.phone
        profile.save()

        clinic.user = user
        if commit:
            clinic.save()

        self.created_username = username
        self.password_changed = True
        return clinic

        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome da Clínica'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'


class VeterinarianForm(forms.ModelForm):
    password = forms.CharField(
        label="Senha",
        required=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Senha para login"})
    )

    class Meta:
        model = Veterinarian
        fields = ['name', 'email', 'phone', 'password']
        
    def clean_password(self):
        pwd = self.cleaned_data.get("password") or ""
        pwd = pwd.strip()
        if pwd and len(pwd) < 6:
            raise forms.ValidationError("A senha deve ter pelo menos 6 caracteres.")
        return pwd

    def save(self, commit=True):
        vet = super().save(commit=False)
        pwd = (self.cleaned_data.get("password") or "").strip()

        # flags/mensagens para a view (opcional)
        self.created_username = None
        self.updated_username = None
        self.password_changed = False

        existing_user = getattr(self.instance, "user", None)

        # =========================
        # CASO 1: Já existe usuário
        # =========================
        if existing_user:
            base = _to_login_base(vet.name)
            new_username = _make_unique_username(base, exclude_user_id=existing_user.id)

            # Se mudou o nome → atualiza username
            if existing_user.username != new_username:
                existing_user.username = new_username
                self.updated_username = new_username

            # Atualiza dados básicos do usuário
            existing_user.first_name = vet.name
            if vet.email:
                existing_user.email = vet.email or ""
            existing_user.save()

            # Garante que role continua BASIC e atualiza whatsapp
            profile, _ = Profile.objects.get_or_create(user=existing_user)
            profile.role = 'BASIC'
            if vet.phone:
                profile.whatsapp = vet.phone or ""
            profile.save()

            # Se foi digitada senha, atualiza
            if pwd:
                existing_user.set_password(pwd)
                existing_user.save()
                self.password_changed = True

            # Mantém vínculo
            vet.user = existing_user

            if commit:
                vet.save()

            # Atualiza a coluna exibida na tabela de exames
            from .models import Exam
            Exam.objects.filter(assigned_user=existing_user).update(clinic_or_vet=vet.name)

            return vet

        # =====================================
        # CASO 2: NÃO existe usuário ainda (vet.user é None)
        # =====================================

        # Se não informou senha → salva só o veterinário, sem criar login
        if not pwd:
            if commit:
                vet.save()
            return vet

        # Se informou senha → cria usuário e vincula
        base = _to_login_base(vet.name)
        username = _make_unique_username(base)

        user = User(username=username)
        user.first_name = vet.name
        if vet.email:
            user.email = vet.email
        user.set_password(pwd)
        user.save()

        profile, _ = Profile.objects.get_or_create(user=user)
        profile.role = 'BASIC'
        if vet.phone:
            profile.whatsapp = vet.phone
        profile.save()

        vet.user = user
        if commit:
            vet.save()

        self.created_username = username
        self.password_changed = True
        return vet

        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do Veterinário'})
        self.fields['email'].widget.attrs.update({'placeholder': 'exemplo@email.com'})
        self.fields['phone'].widget.attrs.update({'placeholder': '(XX) XXXX-XXXX'})
        self.fields['phone'].help_text = 'Formato: (XX) 9XXXX-XXXX ou (XX) XXXX-XXXX'


class PetForm(forms.ModelForm):
    class Meta:
        model = Pet
        fields = ['name', 'breed', 'tutor']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['name'].widget.attrs.update({'placeholder': 'Nome do Pet'})
        self.fields['breed'].widget.attrs.update({'placeholder': 'Raça do Pet'})

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
        label="Arquivos PDF",
        required=True,
        widget=MultipleFileInput(attrs={"multiple": True})
    )

    MAX_FILES = 50

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

