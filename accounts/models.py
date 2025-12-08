from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    whatsapp = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)

    def __str__(self):
        return f'Perfil de {self.user.username}'


class Exam(models.Model):
    date_realizacao = models.DateField("Data de realização")
    clinic_or_vet = models.CharField("Clínica / Veterinário", max_length=255)
    exam_type = models.CharField("Exame", max_length=255)
    pet_name = models.CharField("Pet", max_length=255)
    breed = models.CharField("Raça", max_length=255, blank=True)
    tutor_name = models.CharField("Tutor", max_length=255)

    alerta_email = models.DateTimeField("Alerta Email", blank=True, null=True)
    alerta_zap = models.DateTimeField("Alerta Zap", blank=True, null=True)
    retorno_previsto = models.DateField("Retorno previsto", blank=True, null=True)

    # quem cadastrou o exame (pode ser útil depois)
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


