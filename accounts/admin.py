from django.contrib import admin
from .models import Profile, Exam


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'whatsapp')


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = (
        'date_realizacao',
        'clinic_or_vet',
        'exam_type',
        'pet_name',
        'tutor_name',
        'retorno_previsto',
    )
    list_filter = ('exam_type', 'clinic_or_vet')
    search_fields = ('pet_name', 'tutor_name', 'clinic_or_vet', 'exam_type')

