from django.db import models
from django.contrib.auth.models import User
from django.conf import settings


class Profile(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('BASIC', 'Básico'),
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


class Tutor(models.Model):
    name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # NOVOS CAMPOS
    surname = models.CharField("Sobrenome", max_length=255, blank=True)
    identification = models.CharField("Complemento", max_length=255, blank=True)

    @property
    def display_name(self):
        base = self.name.strip()
        if self.surname:
            base = f"{base} {self.surname.strip()}"
        if self.identification:
            base = f"{base} ({self.identification.strip()})"
        return base

    def __str__(self):
        return self.display_name


class Clinic(BaseContact):
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clinic_account'
    )

    class Meta(BaseContact.Meta):
        verbose_name = "Clínica"
        verbose_name_plural = "Clínicas"


class Veterinarian(BaseContact):
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vet_account'
    )

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



