from django.db import models
from django.contrib.auth.models import User
from django.conf import settings


class Profile(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('ADMIN_AUX', 'Administrador Auxiliar'),
        ('BASIC', 'Básico'),
        ('TUTOR', 'Tutor'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    whatsapp = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='BASIC')

    def __str__(self):
        return f'Perfil de {self.user.username}'


class Exam(models.Model):
    date_realizacao = models.DateField("Data de realização")
    clinic_or_vet = models.CharField("Clínica / Veterinário", max_length=255)
    exam_type = models.CharField("Exame", max_length=255)
    pet_name = models.CharField("Pet", max_length=255)
    breed = models.CharField("Raça", max_length=255, blank=True)
    tutor_name = models.CharField("Tutor", max_length=255)

    tutor_phone = models.CharField("Celular do tutor", max_length=20, blank=True)
    tutor_email = models.CharField("E-mail do tutor", max_length=255, blank=True)
    observations = models.TextField("Observações", blank=True)

    alerta_email = models.DateTimeField("Alerta Email", blank=True, null=True)
    alerta_zap = models.DateTimeField("Alerta Zap", blank=True, null=True)
    retorno_previsto = models.DateField("Retorno previsto", blank=True, null=True)
    
    additional_clinic_or_vet = models.JSONField(
        "Clínicas/Vets adicionais",
        default=list,
        blank=True,
    )

    pdf_file = models.FileField("Arquivo PDF", upload_to='exam_pdfs/', blank=True, null=True)

    assigned_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_exams",
        help_text="Conta (clínica/vet) que pode visualizar este exame"
    )

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exams"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    
    def get_additional_clinic_or_vet_names(self):
        """
        Retorna lista de nomes extras resolvidos.
          - se tiver clínica(s) e vet(s): clínicas primeiro, depois vets
          - se tiver só um tipo: mantém a ordem em que foi selecionado
        """
        tokens = self.additional_clinic_or_vet or []
        resolved = []  # lista de tuplas ("C"|"V", "Nome")

        for token in tokens:
            try:
                kind, raw_id = token.split(":", 1)
                obj_id = int(raw_id)
            except Exception:
                continue

            if kind == "CLINIC":
                c = Clinic.objects.filter(id=obj_id).first()
                if c:
                    resolved.append(("C", c.display_name))
            elif kind == "VET":
                v = Veterinarian.objects.filter(id=obj_id).first()
                if v:
                    resolved.append(("V", v.display_name))

        clinic_names = [name for k, name in resolved if k == "C"]
        vet_names = [name for k, name in resolved if k == "V"]

        if clinic_names and vet_names:
            return clinic_names + vet_names

        return [name for _, name in resolved]

    @property
    def additional_clinic_or_vet_display(self):
        names = self.get_additional_clinic_or_vet_names()
        return ", ".join(names)

    class Meta:
        ordering = ['-date_realizacao', '-created_at']

    def __str__(self):
        return f'{self.exam_type} - {self.pet_name}'
        
class BaseContact(models.Model):
    name = models.CharField("Nome", max_length=255)
    email = models.CharField("E-mail", max_length=255, blank=True)
    phone = models.CharField("Telefone", max_length=20, blank=True)
    created_at = models.DateTimeField("Data de cadastro", auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['name']

    def __str__(self):
        return self.name


class Tutor(BaseContact):
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    surname = models.CharField("Sobrenome", max_length=255, blank=True)

    @property
    def display_name(self):
        base = self.name.strip()
        if self.surname:
            base = f"{base} {self.surname.strip()}"
        return base

    def __str__(self):
        return self.display_name
        
    class Meta(BaseContact.Meta):
        verbose_name = "Tutor"
        verbose_name_plural = "Tutores"


class Clinic(models.Model):
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)

    @property
    def display_name(self):
        return self.name.strip()

    def __str__(self):
        return self.display_name

    class Meta(BaseContact.Meta):
        verbose_name = "Clínica"
        verbose_name_plural = "Clínicas"


class Veterinarian(models.Model):
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
    
    surname = models.CharField("Sobrenome", max_length=255, blank=True)

    @property
    def display_name(self):
        base = self.name.strip()
        if self.surname:
            base = f"{base} {self.surname.strip()}"
        return base

    def __str__(self):
        return self.display_name

    class Meta(BaseContact.Meta):
        verbose_name = "Veterinário"
        verbose_name_plural = "Veterinários"


class Pet(models.Model):
    name = models.CharField("Nome", max_length=255)
    breed = models.CharField("Raça", max_length=255, blank=True)
    tutor = models.ForeignKey(Tutor, on_delete=models.CASCADE, related_name="pets")
    created_at = models.DateTimeField("Data de cadastro", auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
        
class ExamTypeAlias(models.Model):
    abbreviation = models.CharField("Sigla", max_length=50, unique=True)
    full_name = models.CharField("Exame", max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["abbreviation"]

    def save(self, *args, **kwargs):
        # garante case-insensitive na prática (sempre salva em minúsculo)
        if self.abbreviation:
            self.abbreviation = self.abbreviation.strip().lower()
        if self.full_name:
            self.full_name = self.full_name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.abbreviation} -> {self.full_name}"

class ExamExtraPDF(models.Model):
    exam = models.ForeignKey(
        Exam,
        on_delete=models.CASCADE,
        related_name="extra_pdfs",
    )
    file = models.FileField(upload_to="exam_pdfs/extras/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Extra PDF ({self.exam_id})"



