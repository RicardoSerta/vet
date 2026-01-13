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


class Tutor(BaseContact):
    class Meta(BaseContact.Meta):
        verbose_name = "Tutor"
        verbose_name_plural = "Tutores"


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


