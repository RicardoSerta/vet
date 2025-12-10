from django.contrib import admin
from .models import Profile, Exam, Tutor, Clinic, Veterinarian, Pet


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


@admin.register(Tutor)
class TutorAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'created_at')
    search_fields = ('name', 'email', 'phone')


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'created_at')
    search_fields = ('name', 'email', 'phone')


@admin.register(Veterinarian)
class VeterinarianAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'created_at')
    search_fields = ('name', 'email', 'phone')


@admin.register(Pet)
class PetAdmin(admin.ModelAdmin):
    list_display = ('name', 'breed', 'tutor', 'created_at')
    search_fields = ('name', 'breed', 'tutor__name')

