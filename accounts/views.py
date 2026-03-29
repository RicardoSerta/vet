from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.utils.encoding import force_str, force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.text import slugify
from django.utils import timezone
from django.conf import settings
from .notifications import (
    send_exam_email,
    send_tutor_exam_email,
    send_provider_exam_email,
    send_provider_bulk_exam_email,
    send_provider_return_email,
    send_portal_access_email,
    send_contact_updated_email,
)
from .whatsapp_client import (
    normalize_br_phone,
    send_exam_whatsapp,
    send_tutor_exam_whatsapp,
    send_provider_exam_whatsapp,
    send_provider_bulk_exam_whatsapp,
    send_provider_return_whatsapp,
    send_portal_access_whatsapp,
    send_contact_updated_whatsapp,
)
from django.contrib import messages
from django.db.models import Q
from django.urls import reverse
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from .authz import admin_required, is_admin_user, is_superadmin_user, superadmin_required
import os
import mimetypes
import re
import unicodedata
from .models import Profile, Exam, Tutor, Clinic, Veterinarian, Pet, ExamTypeAlias, ExamExtraPDF
from .forms import ExamUploadForm, TutorForm, ClinicForm, VeterinarianForm, PetForm, MultiExamUploadForm, parse_exam_filename, ExamTypeAliasForm, AdminAuxForm

MANAGEMENT_CATEGORIES = {
    'tutores': {
        'label': 'Tutores',
        'singular': 'Tutor',
        'model': Tutor,
        'form': TutorForm,
        'search_fields': ['name', 'email', 'phone'],
        'order_map': {
            'nome': 'name',
            'email': 'email',
            'telefone': 'phone',
            'data': 'created_at',
        },
        'empty_message': 'Nenhum tutor cadastrado ainda.',
    },
    'clinicas': {
        'label': 'Clínicas',
        'singular': 'Clínica',
        'model': Clinic,
        'form': ClinicForm,
        'search_fields': ['name', 'email', 'phone'],
        'order_map': {
            'nome': 'name',
            'email': 'email',
            'telefone': 'phone',
            'data': 'created_at',
        },
        'empty_message': 'Nenhuma clínica cadastrada ainda.',
    },
    'veterinarios': {
        'label': 'Veterinários',
        'singular': 'Veterinário',
        'model': Veterinarian,
        'form': VeterinarianForm,
        'search_fields': ['name', 'email', 'phone'],
        'order_map': {
            'nome': 'name',
            'email': 'email',
            'telefone': 'phone',
            'data': 'created_at',
        },
        'empty_message': 'Nenhum veterinário cadastrado ainda.',
    },
    'pets': {
        'label': 'Pets',
        'singular': 'Pet',
        'model': Pet,
        'form': PetForm,
        'search_fields': ['name', 'breed', 'tutor__name'],
        'order_map': {
            'nome': 'name',
            'raca': 'breed',
            'tutor': 'tutor__name',
            'data': 'created_at',
        },
        'empty_message': 'Nenhum pet cadastrado ainda.',
    },
    'admin': {
        'label': 'Admin',
        'singular': 'Admin',
        'empty_message': 'Nenhum administrador cadastrado ainda.',
    },
}

def ensure_tutor_and_pet(tutor_name, pet_name, breed, tutor_email="", tutor_phone=""):
    """
    Garante que:
    - Existe um Tutor com esse nome (case-insensitive).
      Se existir e email/phone vierem preenchidos, preenche apenas se estiver vazio.
    - Existe um Pet (nome + tutor) (case-insensitive no nome do pet).
      Se existir e a raça estiver vazia/SRD, atualiza se vier uma raça melhor.
    """
    tutor_name = (tutor_name or "").strip()
    pet_name = (pet_name or "").strip()
    breed = (breed or "").strip() or "SRD"
    tutor_email = (tutor_email or "").strip()
    tutor_phone = (tutor_phone or "").strip()

    if not tutor_name or not pet_name:
        return None, None

    # 1) Tutor
    tutor = Tutor.objects.filter(name__iexact=tutor_name).first()
    if not tutor:
        tutor = Tutor.objects.create(
            name=tutor_name,
            email=tutor_email,
            phone=tutor_phone
        )
    else:
        changed = False
        if tutor_email and not tutor.email:
            tutor.email = tutor_email
            changed = True
        if tutor_phone and not tutor.phone:
            tutor.phone = tutor_phone
            changed = True
        if changed:
            tutor.save()

    # 2) Pet (nome + tutor)
    pet = Pet.objects.filter(name__iexact=pet_name, tutor=tutor).first()
    if not pet:
        pet = Pet.objects.create(
            name=pet_name,
            breed=breed or "SRD",
            tutor=tutor
        )
    else:
        # melhora raça se antes estava SRD/vazia
        if (not pet.breed or pet.breed.upper() == "SRD") and breed and breed.upper() != "SRD":
            pet.breed = breed
            pet.save()

    return tutor, pet
    
def _to_login_base(name: str) -> str:
    name = (name or "").strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join([c for c in name if not unicodedata.combining(c)])
    name = re.sub(r"\s+", ".", name)
    name = re.sub(r"[^a-z0-9@.+-_]", "", name)
    return name or "user"

def _make_unique_username(base: str) -> str:
    username = base
    i = 2
    while User.objects.filter(username=username).exists():
        username = f"{base}{i}"
        i += 1
    return username

def build_activation_link(request, user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("activate_account", args=[uidb64, token])
    return request.build_absolute_uri(path)

def ensure_pending_user_for_provider(name: str, email: str, phone: str, role: str):
    """
    Cria (se necessário) um User com senha inutilizável.
    Retorna (user, created_now, needs_activation)
    """
    email = (email or "").strip()
    phone = (phone or "").strip()
    name = (name or "").strip()

    # Só cria user se tiver algum contato (email OU telefone)
    if not email and not phone:
        return None, False, False

    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()

    created_now = False
    if not user:
        base = _to_login_base(email.split("@")[0] if email else name)
        username = _make_unique_username(base)
        user = User(username=username, email=email, first_name=name)
        user.set_unusable_password()
        user.save()
        created_now = True

    profile, _ = Profile.objects.get_or_create(user=user)
    profile.role = role
    if phone and not profile.whatsapp:
        profile.whatsapp = phone
    profile.save()

    needs_activation = not user.has_usable_password()
    return user, created_now, needs_activation
    
def _notification_label(name: str, category: str | None = None) -> str:
    name = (name or "").strip()
    if not name:
        return "cliente"
    if category == "clinicas":
        return name
    return name.split()[0]
    
def translate_exam_type(exam_type_raw: str) -> str:
    key = (exam_type_raw or "").strip().lower()
    if not key:
        return exam_type_raw
    alias = ExamTypeAlias.objects.filter(abbreviation=key).first()
    return alias.full_name if alias else exam_type_raw
    
def user_can_view_exam(user, exam) -> bool:
    if is_admin_user(user):
        return True

    profile, _ = Profile.objects.get_or_create(user=user)

    if profile.role == "TUTOR":
        return _tutor_matches_exam(user, profile, exam)

    return user_is_provider_for_exam(user, exam)
    
def user_is_provider_for_exam(user, exam) -> bool:
    # principal
    if exam.assigned_user_id == user.id:
        return True

    # adicionais: exam.additional_clinic_or_vet é lista ["CLINIC:1", "VET:3"]
    profile, _ = Profile.objects.get_or_create(user=user)
    if profile.role != "BASIC":
        return False

    tokens = exam.additional_clinic_or_vet or []

    # se for user de clínica:
    clinic = Clinic.objects.filter(user=user).first()
    if clinic and f"CLINIC:{clinic.id}" in tokens:
        return True

    # se for user de vet:
    vet = Veterinarian.objects.filter(user=user).first()
    if vet and f"VET:{vet.id}" in tokens:
        return True

    return False
    
def is_whatsapp_phone(phone: str) -> bool:
    return bool(normalize_br_phone(phone))
    
def get_provider_from_token(selected_value: str):
    selected_value = (selected_value or "").strip()
    if not selected_value:
        return None

    if selected_value.startswith("CLINIC:"):
        clinic_id = int(selected_value.split(":", 1)[1])
        clinic = Clinic.objects.get(id=clinic_id)
        return {
            "token": selected_value,
            "kind": "clinic",
            "obj": clinic,
            "label": clinic.name,
            "email": (clinic.email or "").strip(),
            "phone": (clinic.phone or "").strip(),
            "user": clinic.user,
        }

    if selected_value.startswith("VET:"):
        vet_id = int(selected_value.split(":", 1)[1])
        vet = Veterinarian.objects.get(id=vet_id)
        return {
            "token": selected_value,
            "kind": "vet",
            "obj": vet,
            "label": vet.name,
            "email": (vet.email or "").strip(),
            "phone": (vet.phone or "").strip(),
            "user": vet.user,
        }

    return None


def prepare_provider_for_notification(request, selected_value: str, *, allow_create_user: bool):
    provider = get_provider_from_token(selected_value)
    if not provider:
        return None

    user = provider["user"]
    activation_link = None

    if user is None and allow_create_user:
        u, created_now, needs_activation = ensure_pending_user_for_provider(
            name=provider["label"],
            email=provider["email"],
            phone=provider["phone"],
            role="BASIC",
        )
        if u:
            provider["obj"].user = u
            provider["obj"].save(update_fields=["user"])
            user = u
            if needs_activation:
                activation_link = build_activation_link(request, u)

    elif user and not user.has_usable_password() and allow_create_user:
        activation_link = build_activation_link(request, user)

    provider["user"] = user
    provider["activation_link"] = activation_link
    return provider
    
def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")

def _tutor_matches_exam(user, profile, exam) -> bool:
    user_email = (user.email or "").strip().lower()
    exam_email = (exam.tutor_email or "").strip().lower()

    user_phone = _phone_digits(profile.whatsapp or "")
    exam_phone = _phone_digits(exam.tutor_phone or "")

    email_match = bool(user_email and exam_email and user_email == exam_email)
    phone_match = bool(user_phone and exam_phone and user_phone == exam_phone)

    return email_match or phone_match

def send_simple_email(to_email: str, subject: str, body: str):
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [to_email],
        fail_silently=False
    )

def login_view(request):
    if request.user.is_authenticated:
        return redirect('meu_perfil')

    error = None

    if request.method == 'POST':
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=identifier, password=password)

        if user is None:
            # tenta achar por email
            u = User.objects.filter(email__iexact=identifier).first()
            if u:
                user = authenticate(request, username=u.username, password=password)

        if user is not None:
            login(request, user)
            return redirect("exames")
        else:
            messages.error(request, "Usuário/e-mail ou senha inválidos.")

    return render(request, 'accounts/login.html', {'error': error})


@login_required
def profile_view(request):
    # Garante que exista um Profile para o usuário logado
    profile, created = Profile.objects.get_or_create(user=request.user)
    
    old_email = request.user.email or ""
    old_whatsapp = profile.whatsapp or ""

    if request.method == 'POST':
        name = request.POST.get('name') or ''
        email = request.POST.get('email') or ''
        whatsapp = request.POST.get('whatsapp') or ''
        new_password = request.POST.get('password') or ''
        photo_file = request.FILES.get('photo')

        # Atualiza dados básicos do User
        request.user.first_name = name
        request.user.email = email
        request.user.save()
        
        # Se for tutor e mudou o email, atualiza os exames antigos para manter acesso
        if profile.role == "TUTOR":
            new_email = (request.user.email or "").strip()
            new_whatsapp = (whatsapp or "").strip()

            if old_email.strip() and new_email and old_email.strip().lower() != new_email.lower():
                Exam.objects.filter(tutor_email__iexact=old_email.strip()).update(tutor_email=new_email)

            if old_whatsapp.strip() and new_whatsapp and _phone_digits(old_whatsapp) != _phone_digits(new_whatsapp):
                Exam.objects.filter(tutor_phone__iexact=old_whatsapp.strip()).update(tutor_phone=new_whatsapp)

        # Atualiza dados do Profile
        profile.whatsapp = whatsapp
        if photo_file:
            profile.photo = photo_file
        profile.save()

        # Troca de senha (se veio algo no campo)
        if new_password:
            request.user.set_password(new_password)
            request.user.save()
            # Mantém o usuário logado após trocar a senha
            update_session_auth_hash(request, request.user)
            
        email_changed = (old_email or "").strip().lower() != (email or "").strip().lower()
        phone_changed = _phone_digits(old_whatsapp) != _phone_digits(whatsapp)
        contacts_changed = email_changed or phone_changed

        category_hint = "clinicas" if Clinic.objects.filter(user=request.user).exists() else None
        recipient_label = _notification_label(name, category_hint)

        if contacts_changed:
            if email:
                try:
                    send_contact_updated_email(
                        request,
                        to_email=email,
                        recipient_label=recipient_label,
                        email_value=email,
                        phone_value=whatsapp,
                    )
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail de atualização: {e}")

            if whatsapp and is_whatsapp_phone(whatsapp):
                try:
                    ok = send_contact_updated_whatsapp(
                        request,
                        to_phone=whatsapp,
                        recipient_label=recipient_label,
                        email_value=email,
                        phone_value=whatsapp,
                    )
                    if not ok:
                        messages.warning(request, "O WhatsApp de atualização não foi enviado.")
                except Exception as e:
                    messages.error(request, f"Falha ao enviar WhatsApp de atualização: {e}")

        messages.success(request, 'Dados atualizados com sucesso!')
        return redirect('meu_perfil')

    context = {
        'profile': profile,
    }
    return render(request, 'accounts/profile.html', context)
    
@login_required
def exam_pdf(request, pk):
    exam = get_object_or_404(Exam, pk=pk)

    if not user_can_view_exam(request.user, exam):
        raise Http404()

    if not exam.pdf_file:
        raise Http404()

    return FileResponse(exam.pdf_file.open('rb'), content_type='application/pdf')
    
@login_required
def exams_list(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    exams = Exam.objects.all()
    tutor_filter_later = False

    if not is_admin_user(request.user):
        if profile.role == "TUTOR":
            tutor_filter_later = True
        else:
            # Clínica/Vet (BASIC)
            exams = exams.filter(assigned_user=request.user)

    # Busca simples
    search_query = request.GET.get('q', '').strip()
    if search_query:
        exams = exams.filter(
            Q(clinic_or_vet__icontains=search_query) |
            Q(exam_type__icontains=search_query) |
            Q(pet_name__icontains=search_query) |
            Q(breed__icontains=search_query) |
            Q(tutor_name__icontains=search_query)
        )

    # Ordenação
    order = request.GET.get('order', '')
    direction = request.GET.get('direction', 'asc')

    order_map = {
        'realizacao': 'date_realizacao',
        'clinica': 'clinic_or_vet',
        'exame': 'exam_type',
        'pet': 'pet_name',
        'raca': 'breed',
        'tutor': 'tutor_name',
        'cadastro': 'created_at',
        'retorno': 'retorno_previsto',
    }

    if order in order_map:
        field_name = order_map[order]
        if direction == 'desc':
            field_name = '-' + field_name
        exams = exams.order_by(field_name)
        
    if tutor_filter_later:
        exams = [exam for exam in exams if _tutor_matches_exam(request.user, profile, exam)]

    context = {
        'profile': profile,
        'exams': exams,
        'search_query': search_query,
        'order': order,
        'direction': direction,
    }
    return render(request, 'accounts/exams_list.html', context)
    
@login_required
def exam_detail(request, pk):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    exam = get_object_or_404(Exam, pk=pk)
    
    
    if not user_can_view_exam(request.user, exam):
        messages.error(request, "Você não tem permissão para visualizar este exame.")
        return redirect('exames')

    return render(request, 'accounts/exam_detail.html', {
        'profile': profile,
        'exam': exam,
    })


@login_required
@admin_required
def exam_delete(request, pk):
    exam = get_object_or_404(Exam, pk=pk)

    if request.method == 'POST':
        exam.delete()
        messages.success(request, 'Exame excluído com sucesso.')
        return redirect('exames')

    # Se acessarem por GET, mostra só uma confirmação simples
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return render(request, 'accounts/exam_confirm_delete.html', {
        'profile': profile,
        'exam': exam,
    })


@login_required
@admin_required
def exam_forward(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    # Protótipo: só mostra uma mensagem por enquanto
    messages.info(request, 'Funcionalidade de encaminhar exame ainda será implementada.')
    return redirect('exames')
    
@login_required
@admin_required
def exam_upload(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = ExamUploadForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            
            selected = cd["clinic_or_vet"]
            notify_provider = (cd.get("notify_provider") or "") == "1"
            tutor_activation_link = None

            provider_tokens = [selected] + (cd.get("additional_clinic_or_vet") or [])
            seen_tokens = set()
            provider_tokens = [
                token for token in provider_tokens
                if token and not (token in seen_tokens or seen_tokens.add(token))
            ]

            main_provider = prepare_provider_for_notification(
                request,
                selected,
                allow_create_user=notify_provider,
            )

            assigned_user = main_provider["user"] if main_provider else None
            clinic_or_vet_name = main_provider["label"] if main_provider else ""

            additional_providers = []
            if notify_provider:
                for token in provider_tokens[1:]:
                    provider = prepare_provider_for_notification(
                        request,
                        token,
                        allow_create_user=True,
                    )
                    if provider:
                        additional_providers.append(provider)
                
            ensure_tutor_and_pet(
                tutor_name=cd['parsed_tutor_name'],
                pet_name=cd['parsed_pet_name'],
                breed=cd['parsed_breed'],
                tutor_email=cd.get('tutor_email', ''),
                tutor_phone=cd.get('tutor_phone', ''),
            )

            tutor_user = None
            tutor_email = (cd.get("tutor_email") or "").strip()
            tutor_phone = (cd.get("tutor_phone") or "").strip()

            notify_tutor_email = (cd.get("notify_tutor_email") or "1") != "0"
            if not tutor_email:
                notify_tutor_email = False

            notify_tutor_phone = (cd.get("notify_tutor_phone") or "1") != "0"
            if not tutor_phone:
                notify_tutor_phone = False

            if (tutor_email or tutor_phone) and (notify_tutor_email or notify_tutor_phone):
                tutor_user, created_now, needs_activation = ensure_pending_user_for_provider(
                    name=cd["parsed_tutor_name"],
                    email=tutor_email,
                    phone=tutor_phone,
                    role="TUTOR",
                )
                if tutor_user and needs_activation:
                    tutor_activation_link = build_activation_link(request, tutor_user)

            exam = Exam.objects.create(
                date_realizacao=cd['parsed_date_realizacao'],
                clinic_or_vet=clinic_or_vet_name,
                exam_type=translate_exam_type(cd['parsed_exam_type']),
                pet_name=cd['parsed_pet_name'],
                breed=cd['parsed_breed'],
                tutor_name=cd['parsed_tutor_name'],
                tutor_phone=cd['tutor_phone'],
                tutor_email=cd['tutor_email'],
                retorno_previsto=cd.get('retorno_previsto'),
                retorno_horario=cd.get('retorno_horario'),
                observations=cd['observations'],
                pdf_file=cd['pdf_file'],
                owner=request.user,
                assigned_user=assigned_user,
                additional_clinic_or_vet=cd.get("additional_clinic_or_vet") or [],
            )
            
            tutor_email_sent_any = False
            tutor_zap_sent_any = False
            provider_sent_any = False

            if tutor_email and notify_tutor_email:
                try:
                    ok = send_tutor_exam_email(
                        request,
                        exam=exam,
                        to_email=tutor_email,
                        activation_link=tutor_activation_link,
                    )
                    tutor_email_sent_any = tutor_email_sent_any or ok
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail para o tutor: {e}")

            # 1b) Tutor por WhatsApp
            if tutor_phone and notify_tutor_phone and is_whatsapp_phone(tutor_phone):
                try:
                    ok = send_tutor_exam_whatsapp(
                        request,
                        exam=exam,
                        to_phone=tutor_phone,
                        activation_link=tutor_activation_link,
                    )
                    tutor_zap_sent_any = tutor_zap_sent_any or ok
                except Exception as e:
                    messages.error(request, f"Falha ao enviar WhatsApp para o tutor: {e}")

            # 2) Clínica/Veterinário principal + adicionais (somente se o botão estiver ativado)
            if notify_provider:
                provider_targets = []
                if main_provider:
                    provider_targets.append(main_provider)
                provider_targets.extend(additional_providers)

                seen_tokens = set()
                deduped_targets = []
                for provider in provider_targets:
                    token = provider.get("token")
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    deduped_targets.append(provider)

                for provider in deduped_targets:
                    provider_email = provider["email"]
                    provider_phone = provider["phone"]
                    provider_label = provider["label"]
                    provider_activation_link = provider.get("activation_link")

                    if provider_email:
                        try:
                            send_provider_exam_email(
                                request,
                                exam=exam,
                                to_email=provider_email,
                                recipient_label=provider_label,
                                activation_link=provider_activation_link,
                            )
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar e-mail para {provider_label}: {e}")

                    if provider_phone and is_whatsapp_phone(provider_phone):
                        try:
                            send_provider_exam_whatsapp(
                                request,
                                exam=exam,
                                to_phone=provider_phone,
                                recipient_label=provider_label,
                                activation_link=provider_activation_link,
                            )
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar WhatsApp para {provider_label}: {e}")

            # Campos legado agora representam alerta do tutor
            if tutor_email_sent_any:
                exam.alerta_email = timezone.now()
                exam.save(update_fields=["alerta_email"])

            if tutor_zap_sent_any:
                exam.alerta_zap = timezone.now()
                exam.save(update_fields=["alerta_zap"])
                
            if provider_sent_any:
                exam.alerta_provider = True
                exam.save(update_fields=["alerta_provider"])

            messages.success(
                request,
                f'Exame de {exam.pet_name} cadastrado com sucesso.'
            )
            extra_files = form.cleaned_data.get("extra_files", [])
            for f in extra_files:
                ExamExtraPDF.objects.create(exam=exam, file=f)
            return redirect('exames')
    else:
        form = ExamUploadForm()

    return render(request, 'accounts/exam_upload.html', {
        'profile': profile,
        'form': form,
    })
    
@login_required
@admin_required
def exam_upload_multi(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = MultiExamUploadForm(request.POST, request.FILES)

        if form.is_valid():
            selected = form.cleaned_data["clinic_or_vet"]
            pdf_files = form.cleaned_data["pdf_files"]
            notify_provider = (form.cleaned_data.get("notify_provider") or "") == "1"

            main_provider = prepare_provider_for_notification(
                request,
                selected,
                allow_create_user=notify_provider,
            )

            assigned_user = main_provider["user"] if main_provider else None
            clinic_or_vet_name = main_provider["label"] if main_provider else ""
            provider_activation_link = main_provider.get("activation_link") if main_provider else None
            provider_email = (main_provider.get("email") or "").strip() if main_provider else ""
            provider_phone = (main_provider.get("phone") or "").strip() if main_provider else ""
            provider_label = main_provider["label"] if main_provider else "Clínica/Veterinário"

            created_count = 0
            created_exam_ids = []
            first_exam = None

            with transaction.atomic():
                for f in pdf_files:
                    data = parse_exam_filename(f.name)

                    ensure_tutor_and_pet(
                        tutor_name=data["tutor_name"],
                        pet_name=data["pet_name"],
                        breed=data["breed"],
                    )

                    exam = Exam.objects.create(
                        date_realizacao=data["date_realizacao"],
                        clinic_or_vet=clinic_or_vet_name,
                        exam_type=translate_exam_type(data["exam_type"]),
                        pet_name=data["pet_name"],
                        breed=data["breed"],
                        tutor_name=data["tutor_name"],
                        pdf_file=f,
                        owner=request.user,
                        assigned_user=assigned_user,
                        tutor_phone="",
                        tutor_email="",
                        observations="",
                    )

                    if first_exam is None:
                        first_exam = exam

                    created_exam_ids.append(exam.id)
                    created_count += 1

            provider_sent_any = False

            # Só notifica se o botão estiver ativado
            if notify_provider:
                # Se enviou só 1 exame no upload em massa, reutiliza o template normal
                if created_count == 1 and first_exam is not None:
                    if provider_email:
                        try:
                            ok = send_provider_exam_email(
                                request,
                                exam=exam,
                                to_email=provider_email,
                                recipient_label=provider_label,
                                activation_link=provider_activation_link,
                            )
                            provider_sent_any = provider_sent_any or ok
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar e-mail para a clínica/vet: {e}")

                    if provider_phone and is_whatsapp_phone(provider_phone):
                        try:
                            ok = send_provider_exam_whatsapp(
                                request,
                                exam=exam,
                                to_phone=provider_phone,
                                recipient_label=provider_label,
                                activation_link=provider_activation_link,
                            )
                            provider_sent_any = provider_sent_any or ok
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar WhatsApp para a clínica/vet: {e}")

                # Se enviou mais de 1 exame, usa os templates novos de massa
                elif created_count > 1:
                    if provider_email:
                        try:
                            ok = send_provider_bulk_exam_email(
                                request,
                                recipient_label=provider_label,
                                to_email=provider_email,
                                exam_count=created_count,
                                activation_link=provider_activation_link,
                            )
                            provider_sent_any = provider_sent_any or ok
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar e-mail em massa para a clínica/vet: {e}")

                    if provider_phone and is_whatsapp_phone(provider_phone):
                        try:
                            ok = send_provider_bulk_exam_whatsapp(
                                request,
                                recipient_label=provider_label,
                                to_phone=provider_phone,
                                exam_count=created_count,
                                activation_link=provider_activation_link,
                            )
                            provider_sent_any = provider_sent_any or ok
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar WhatsApp em massa para a clínica/vet: {e}")

            # Não marcamos alerta_email/alerta_zap aqui, porque esses campos
            # agora representam alerta do tutor na tela de visualização.
            
            if provider_sent_any:
                Exam.objects.filter(id__in=created_exam_ids).update(alerta_provider=True)

            messages.success(request, f"{created_count} exame(s) enviados com sucesso.")
            return redirect("exames")
    else:
        form = MultiExamUploadForm()

    return render(request, "accounts/exam_upload_multi.html", {"profile": profile, "form": form})
    
@login_required
def exam_view(request, pk):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    exam = get_object_or_404(Exam, pk=pk)

    if not user_can_view_exam(request.user, exam):
        return HttpResponseForbidden("Você não tem permissão para ver este exame.")

    extras = exam.extra_pdfs.all().order_by("uploaded_at")

    provider_obj = Clinic.objects.filter(name__iexact=exam.clinic_or_vet).first()
    if provider_obj is None:
        provider_obj = Veterinarian.objects.filter(name__iexact=exam.clinic_or_vet).first()

    provider_phone = (getattr(provider_obj, "phone", "") or "").strip()
    provider_email = (getattr(provider_obj, "email", "") or "").strip()

    if provider_phone and provider_email:
        provider_contact = f"{provider_phone} | {provider_email}"
    elif provider_phone:
        provider_contact = provider_phone
    elif provider_email:
        provider_contact = provider_email
    else:
        provider_contact = "–"

    return render(request, "accounts/exam_view.html", {
        "profile": profile,
        "exam": exam,
        "extras": extras,
        "is_admin": is_admin_user(request.user),
        "provider_contact": provider_contact,
    })


@login_required
def exam_extra_pdf(request, pk, extra_pk):
    exam = get_object_or_404(Exam, pk=pk)

    if not user_can_view_exam(request.user, exam):
        return HttpResponseForbidden("Você não tem permissão para ver este exame.")

    extra = get_object_or_404(ExamExtraPDF, pk=extra_pk, exam=exam)

    content_type, _ = mimetypes.guess_type(extra.file.name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(extra.file.open("rb"), content_type=content_type)

    filename = os.path.basename(extra.file.name)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response

    
@login_required
@admin_required
def management_view(request, category='tutores'):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    is_superadmin = is_superadmin_user(request.user)

    if category not in MANAGEMENT_CATEGORIES:
        category = 'tutores'
        
    categories_nav = [
        {'slug': key, 'label': value['label']}
        for key, value in MANAGEMENT_CATEGORIES.items()
        if (key != 'admin' or is_superadmin)
    ]
        
    if category == 'admin':
        if not is_superadmin:
            messages.error(request, "Você não tem permissão para acessar essa aba.")
            return redirect('gestao')

        qs = User.objects.filter(
            Q(is_superuser=True) | Q(profile__role__in=['ADMIN', 'ADMIN_AUX'])
        ).distinct()

        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(first_name__icontains=search_query) |
                Q(username__icontains=search_query) |
                Q(email__icontains=search_query) |
                Q(profile__whatsapp__icontains=search_query)
            )

        order = request.GET.get('order', '')
        direction = request.GET.get('direction', 'asc')
        order_map = {
            'nome': 'first_name',
            'telefone': 'profile__whatsapp',
            'email': 'email',
            'data': 'date_joined',
            'funcao': 'profile__role',
        }

        if order in order_map:
            field_name = order_map[order]
            if direction == 'desc':
                field_name = '-' + field_name
            qs = qs.order_by(field_name)
        else:
            qs = qs.order_by('-date_joined')

        items = []
        for u in qs:
            profile, _ = Profile.objects.get_or_create(
                user=u,
                defaults={'role': 'ADMIN' if u.is_superuser else 'ADMIN_AUX'}
            )

            # garante consistência: superuser aparece como ADMIN
            if u.is_superuser and profile.role != 'ADMIN':
                profile.role = 'ADMIN'
                profile.save(update_fields=['role'])

            role_label = 'Administrador' if (u.is_superuser or profile.role == 'ADMIN') else 'Auxiliar'

            # Regra da coluna "Conta?"
            # Auxiliar sem senha = ❌
            # Admin e superuser = ✅
            has_account = True if role_label == 'Administrador' else u.has_usable_password()

            # botão de reenviar alertas:
            # só para auxiliar, sem conta, e com algum meio de contato útil
            can_resend = (
                profile.role == 'ADMIN_AUX'
                and not u.has_usable_password()
                and (
                    bool((u.email or '').strip()) or
                    is_whatsapp_phone(profile.whatsapp or '')
                )
            )

            items.append({
                'id': u.id,
                'name': (u.first_name or u.username),
                'email': (u.email or ''),
                'phone': (profile.whatsapp or ''),
                'created_at': u.date_joined,
                'role_label': role_label,
                'has_account': has_account,
                'can_resend': can_resend,
                'can_delete': (profile.role == 'ADMIN_AUX') and (not u.is_superuser),
                'can_edit': (profile.role == 'ADMIN_AUX') and (not u.is_superuser),
            })
            
            if order == 'conta':
                items.sort(
                    key=lambda x: (x['has_account'], (x['name'] or '').lower()),
                    reverse=(direction == 'desc')
                )

                categories_nav = [
                    {'slug': key, 'label': value['label']}
                    for key, value in MANAGEMENT_CATEGORIES.items()
                    if (key != 'admin' or is_superadmin)
                ]

        return render(request, 'accounts/management.html', {
            'profile': profile,
            'category': 'admin',
            'category_label': MANAGEMENT_CATEGORIES['admin']['label'],
            'category_singular': MANAGEMENT_CATEGORIES['admin']['singular'],
            'categories_nav': categories_nav,
            'items': items,
            'empty_message': MANAGEMENT_CATEGORIES['admin']['empty_message'],
            'search_query': search_query,
            'order': order,
            'direction': direction,
            'is_superadmin': is_superadmin,
        })

    info = MANAGEMENT_CATEGORIES[category]
    Model = info['model']

    items = Model.objects.all()

    # BUSCA
    search_query = request.GET.get('q', '').strip()
    if search_query:
        q_obj = Q()
        for field in info.get('search_fields', []):
            q_obj |= Q(**{f"{field}__icontains": search_query})
        items = items.filter(q_obj)

    # ORDENAÇÃO
    order = request.GET.get('order', '')
    direction = request.GET.get('direction', 'asc')
    order_map = info.get('order_map', {})

    if order in order_map:
        field_name = order_map[order]
        if direction == 'desc':
            field_name = '-' + field_name
        items = items.order_by(field_name)
    else:
        items = items.order_by('-created_at')
        
    # ===== Possui Conta? (Tutores / Clínicas / Veterinários) =====
    if category in ("tutores", "clinicas", "veterinarios"):
        items_list = list(items)

        if category == "tutores":
            # Tutor não tem FK pra user, então usamos o email do tutor
            emails = [((t.email or "").strip().lower()) for t in items_list if (t.email or "").strip()]
            email_to_has = {}

            if emails:
                profiles = Profile.objects.select_related("user").filter(
                    role="TUTOR",
                    user__email__in=emails,
                )
                for p in profiles:
                    em = (p.user.email or "").strip().lower()
                    if em:
                        email_to_has[em] = p.user.has_usable_password()

            for t in items_list:
                em = (t.email or "").strip().lower()
                t.has_account = bool(em and email_to_has.get(em, False))

        else:
            # Clínica/Vet tem obj.user
            for obj in items_list:
                u = getattr(obj, "user", None)
                obj.has_account = bool(u and u.has_usable_password())

        items = items_list
        
        if category in ("tutores", "clinicas", "veterinarios") and order == "conta":
            items.sort(
                key=lambda x: (x.has_account, (getattr(x, "display_name", "") or "").lower()),
                reverse=(direction == "desc")
            )

    categories_nav = [
        {'slug': key, 'label': value['label']}
        for key, value in MANAGEMENT_CATEGORIES.items()
        if (key != 'admin' or is_superadmin)
    ]

    context = {
        'profile': profile,
        'category': category,
        'category_label': info['label'],
        'category_singular': info['singular'],
        'categories_nav': categories_nav,
        'items': items,
        'empty_message': info['empty_message'],
        'search_query': search_query,
        'order': order,
        'direction': direction,
    }
    return render(request, 'accounts/management.html', context)

@login_required
@admin_required
def management_create(request, category):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if category not in MANAGEMENT_CATEGORIES:
        return redirect('gestao')
        
    if category == 'admin':
        return redirect('gestao_admin_create')

    info = MANAGEMENT_CATEGORIES[category]
    FormClass = info['form']

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save()

            remove_photo = (request.POST.get("remove_photo") == "1")
            if remove_photo:
                try:
                    if getattr(obj, "photo", None):
                        obj.photo.delete(save=False)
                except Exception:
                    pass
                obj.photo = None
                obj.save(update_fields=["photo"])

            notify_email = (form.cleaned_data.get("notify_email") or "1") != "0"
            notify_phone = (form.cleaned_data.get("notify_phone") or "1") != "0"

            email = (getattr(obj, "email", "") or "").strip()
            phone = (getattr(obj, "phone", "") or "").strip()
            name = getattr(obj, "display_name", None) or getattr(obj, "name", "")
            recipient_label = _notification_label(name, category)

            if not email:
                notify_email = False

            if not is_whatsapp_phone(phone):
                notify_phone = False

            u = None
            activation_link = None

            if notify_email or notify_phone:
                role = "TUTOR" if category == "tutores" else "BASIC"
                u, created_now, needs_activation = ensure_pending_user_for_provider(
                    name=name,
                    email=email,
                    phone=phone,
                    role=role,
                )

                if u and needs_activation:
                    activation_link = build_activation_link(request, u)

                if category in ("clinicas", "veterinarios") and getattr(obj, "user", None) is None and u:
                    obj.user = u
                    obj.save(update_fields=["user"])

            if notify_email and email and activation_link:
                try:
                    send_portal_access_email(
                        request,
                        to_email=email,
                        recipient_label=recipient_label,
                        activation_link=activation_link,
                        resend=False,
                    )
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail de cadastro: {e}")

            if notify_phone and phone and activation_link:
                try:
                    ok = send_portal_access_whatsapp(
                        request,
                        to_phone=phone,
                        recipient_label=recipient_label,
                        activation_link=activation_link,
                        resend=False,
                    )
                    if not ok:
                        messages.warning(request, "O WhatsApp de cadastro não foi enviado.")
                except Exception as e:
                    messages.error(request, f"Falha ao enviar WhatsApp de cadastro: {e}")

            messages.success(request, f'{info["singular"]} cadastrado(a) com sucesso.')
            return redirect('gestao_category', category=category)

    else:
        form = FormClass()

    return render(request, 'accounts/management_form.html', {
        'profile': profile,
        'category': category,
        'category_label': info['label'],
        'category_singular': info['singular'],
        'form': form,
        'is_edit': False,
        'obj': None,
    })
    
@login_required
@admin_required
def management_edit(request, category, pk):
    category_map = {
        'tutores': {
            'model': Tutor,
            'form': TutorForm,
            'title': 'Editar Tutor',
            'singular': 'Tutor',
        },
        'clinicas': {
            'model': Clinic,
            'form': ClinicForm,
            'title': 'Editar Clínica',
            'singular': 'Clínica',
        },
        'veterinarios': {
            'model': Veterinarian,
            'form': VeterinarianForm,
            'title': 'Editar Veterinário',
            'singular': 'Veterinário',
        },
        'pets': {
            'model': Pet,
            'form': PetForm,
            'title': 'Editar Pet',
            'singular': 'Pet',
        },
    }

    if category not in category_map:
        messages.error(request, "Categoria inválida.")
        return redirect('gestao')

    info = category_map[category]
    obj = get_object_or_404(info['model'], pk=pk)
    
    old_email = (obj.email or "").strip()
    old_phone = (obj.phone or "").strip()

    if request.method == "POST":
        form = info['form'](request.POST, request.FILES, instance=obj)
        if form.is_valid():
            obj = form.save()
            
            remove_photo = (request.POST.get("remove_photo") == "1")
            if remove_photo:
                try:
                    if getattr(obj, "photo", None):
                        obj.photo.delete(save=False)
                except Exception:
                    pass
                obj.photo = None
                obj.save(update_fields=["photo"])
            
            new_email = (obj.email or "").strip()
            new_phone = (obj.phone or "").strip()

            notify_email = (form.cleaned_data.get("notify_email") or "1") != "0"
            notify_phone = (form.cleaned_data.get("notify_phone") or "1") != "0"

            email_changed = (new_email.lower() != old_email.lower()) if (new_email and old_email) else (new_email != old_email)
            phone_changed = (new_phone != old_phone)

            if category in ("tutores", "clinicas", "veterinarios"):
                contacts_changed = email_changed or phone_changed
                name_value = getattr(obj, "display_name", None) or getattr(obj, "name", "")
                recipient_label = _notification_label(name_value, category)

                if contacts_changed and (new_email or new_phone):
                    role = "TUTOR" if category == "tutores" else "BASIC"
                    u, created_now, needs_activation = ensure_pending_user_for_provider(
                        name=name_value,
                        email=new_email,
                        phone=new_phone,
                        role=role,
                    )

                    if category in ("clinicas", "veterinarios") and getattr(obj, "user", None) is None and u:
                        obj.user = u
                        obj.save(update_fields=["user"])

                    if notify_email and new_email:
                        try:
                            send_contact_updated_email(
                                request,
                                to_email=new_email,
                                recipient_label=recipient_label,
                                email_value=new_email,
                                phone_value=new_phone,
                            )
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar e-mail de atualização: {e}")

                    if notify_phone and new_phone and is_whatsapp_phone(new_phone):
                        try:
                            ok = send_contact_updated_whatsapp(
                                request,
                                to_phone=new_phone,
                                recipient_label=recipient_label,
                                email_value=new_email,
                                phone_value=new_phone,
                            )
                            if not ok:
                                messages.warning(request, "O WhatsApp de atualização não foi enviado.")
                        except Exception as e:
                            messages.error(request, f"Falha ao enviar WhatsApp de atualização: {e}")
            updated_username = getattr(form, "updated_username", None)

            msg = f"{info['singular']} atualizado(a) com sucesso."
            if updated_username:
                msg += f" Login atualizado: {updated_username}"

            messages.success(request, msg)
            return redirect('gestao_category', category=category)
    else:
        form = info['form'](instance=obj)

    # se você usa profile na base_app:
    profile, _ = Profile.objects.get_or_create(user=request.user)

    return render(request, "accounts/management_form.html", {
        "profile": profile,
        "form": form,
        "category": category,
        "title": info["title"],
        "is_edit": True,
        "obj": obj,
    })

@login_required
@admin_required
def management_resend_alerts(request, category, pk):
    if request.method != "POST":
        return redirect("gestao_category", category=category)
    
    if category == "admin":
        if not is_superadmin_user(request.user):
            messages.error(request, "Você não tem permissão para isso.")
            return redirect("gestao")

        p = Profile.objects.select_related("user").filter(user_id=pk, role="ADMIN_AUX").first()
        if not p:
            messages.error(request, "Auxiliar não encontrado.")
            return redirect("gestao_category", category="admin")

        name = (p.user.first_name or p.user.username)
        email = (p.user.email or "").strip()
        phone = (p.whatsapp or "").strip()

        if p.user.has_usable_password():
            messages.info(request, f'"{name}" já possui conta ativa.')
            return redirect("gestao_category", category="admin")

        if not email and not is_whatsapp_phone(phone):
            messages.error(request, f'Não é possível reenviar: "{name}" não possui e-mail nem WhatsApp válidos.')
            return redirect("gestao_category", category="admin")
            
        if email:
            try:
                validate_email(email)
            except DjangoValidationError:
                messages.error(request, f'E-mail inválido em "{name}". Corrija o e-mail antes de reenviar.')
                return redirect("gestao_category", category="admin")

        u, created_now, needs_activation = ensure_pending_user_for_provider(
            name=name,
            email=email,
            phone=phone,
            role="ADMIN_AUX",
        )
        if not u:
            messages.error(request, "Não foi possível preparar o usuário para ativação.")
            return redirect("gestao_category", category="admin")

        activation_link = build_activation_link(request, u) if needs_activation else None

        if not activation_link:
            messages.info(request, f'"{name}" já possui conta ativa.')
            return redirect("gestao_category", category="admin")

        recipient_label = _notification_label(name, "admin")

        try:
            if email:
                send_portal_access_email(
                    request,
                    to_email=email,
                    recipient_label=recipient_label,
                    activation_link=activation_link,
                    resend=True,
                )

            if is_whatsapp_phone(phone):
                ok = send_portal_access_whatsapp(
                    request,
                    to_phone=phone,
                    recipient_label=recipient_label,
                    activation_link=activation_link,
                    resend=True,
                )
                if not ok:
                    messages.warning(request, "O WhatsApp de reenvio não foi enviado.")
            messages.success(request, f'Alertas reenviados para "{name}".')
        except Exception as e:
            messages.error(request, f"Falha ao reenviar alertas: {e}")

        return redirect("gestao_category", category="admin")

    if category not in ("tutores", "clinicas", "veterinarios"):
        messages.error(request, "Categoria inválida para reenviar alertas.")
        return redirect("gestao_category", category=category)

    # ===== pega objeto + dados =====
    if category == "tutores":
        obj = get_object_or_404(Tutor, pk=pk)
        name = getattr(obj, "display_name", None) or getattr(obj, "name", "Tutor")
        email = (obj.email or "").strip()
        phone = (obj.phone or "").strip()

        # se já existe conta ativa, não reenviar
        if email:
            p = Profile.objects.select_related("user").filter(
                role="TUTOR",
                user__email__iexact=email,
            ).first()
            if p and p.user and p.user.has_usable_password():
                messages.info(request, f'"{name}" já possui conta ativa. Use "Remover acesso" se quiser.')
                return redirect("gestao_category", category=category)

        if not email and not is_whatsapp_phone(phone):
            messages.error(request, f'Não é possível reenviar: "{name}" não possui e-mail nem WhatsApp válidos.')
            return redirect("gestao_category", category=category)

        if email:
            try:
                validate_email(email)
            except DjangoValidationError:
                messages.error(request, f'E-mail inválido em "{name}". Corrija o e-mail antes de reenviar.')
                return redirect("gestao_category", category=category)

        u, created_now, needs_activation = ensure_pending_user_for_provider(
            name=name,
            email=email,
            phone=phone,
            role="TUTOR",
        )
        if not u:
            messages.error(request, "Não foi possível preparar o usuário para ativação.")
            return redirect("gestao_category", category=category)

        activation_link = build_activation_link(request, u) if needs_activation else None

        if not activation_link:
            messages.info(request, f'"{name}" já possui conta ativa.')
            return redirect("gestao_category", category=category)

        recipient_label = _notification_label(name, category)

        try:
            if email:
                send_portal_access_email(
                    request,
                    to_email=email,
                    recipient_label=recipient_label,
                    activation_link=activation_link,
                    resend=True,
                )

            if is_whatsapp_phone(phone):
                ok = send_portal_access_whatsapp(
                    request,
                    to_phone=phone,
                    recipient_label=recipient_label,
                    activation_link=activation_link,
                    resend=True,
                )
                if not ok:
                    messages.warning(request, "O WhatsApp de reenvio não foi enviado.")
            messages.success(request, f'Alertas reenviados para "{name}".')
        except Exception as e:
            messages.error(request, f"Falha ao reenviar alertas: {e}")

        return redirect("gestao_category", category=category)

    # ===== clinicas / veterinarios =====
    Model = Clinic if category == "clinicas" else Veterinarian
    obj = get_object_or_404(Model, pk=pk)
    name = getattr(obj, "display_name", None) or getattr(obj, "name", "Usuário")
    email = (obj.email or "").strip()
    phone = (obj.phone or "").strip()

    # se já tem conta ativa, não reenviar
    if obj.user and obj.user.has_usable_password():
        messages.info(request, f'"{name}" já possui conta ativa. Use "Remover acesso" se quiser.')
        return redirect("gestao_category", category=category)

    if not email and not is_whatsapp_phone(phone):
        messages.error(request, f'Não é possível reenviar: "{name}" não possui e-mail nem WhatsApp válidos.')
        return redirect("gestao_category", category=category)

    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            messages.error(request, f'E-mail inválido em "{name}". Corrija o e-mail antes de reenviar.')
            return redirect("gestao_category", category=category)

    # garante user pendente e vincula no objeto
    u = obj.user
    if u is None:
        u, created_now, needs_activation = ensure_pending_user_for_provider(
            name=name,
            email=email,
            phone=phone,
            role="BASIC",
        )
        if not u:
            messages.error(request, "Não foi possível preparar o usuário para ativação.")
            return redirect("gestao_category", category=category)

        obj.user = u
        obj.save(update_fields=["user"])
    else:
        needs_activation = not u.has_usable_password()

    activation_link = build_activation_link(request, u) if needs_activation else None

    if not activation_link:
        messages.info(request, f'"{name}" já possui conta ativa.')
        return redirect("gestao_category", category=category)

    recipient_label = _notification_label(name, category)

    try:
        if email:
            send_portal_access_email(
                request,
                to_email=email,
                recipient_label=recipient_label,
                activation_link=activation_link,
                resend=True,
            )

        if is_whatsapp_phone(phone):
            ok = send_portal_access_whatsapp(
                request,
                to_phone=phone,
                recipient_label=recipient_label,
                activation_link=activation_link,
                resend=True,
            )
            if not ok:
                messages.warning(request, "O WhatsApp de reenvio não foi enviado.")
        messages.success(request, f'Alertas reenviados para "{name}".')
    except Exception as e:
        messages.error(request, f"Falha ao reenviar alertas: {e}")

    return redirect("gestao_category", category=category)

@login_required
@admin_required
def management_remove_access(request, category, pk):
    """
    Remove o login (User) mas mantém o item na tabela (Tutor/Clínica/Vet).
    """
    if request.method != "POST":
        return redirect("gestao_category", category=category)

    if category not in ("tutores", "clinicas", "veterinarios"):
        messages.error(request, "Categoria inválida para remover acesso.")
        return redirect("gestao_category", category=category)

    if category == "tutores":
        obj = get_object_or_404(Tutor, pk=pk)
        name = getattr(obj, "display_name", None) or getattr(obj, "name", "Tutor")
        email = (obj.email or "").strip()

        if not email:
            messages.error(request, f'"{name}" não tem e-mail cadastrado, então não há conta para remover.')
            return redirect("gestao_category", category=category)

        qs = User.objects.filter(email__iexact=email, profile__role="TUTOR")
        if not qs.exists():
            messages.info(request, f'"{name}" não possui conta ativa.')
            return redirect("gestao_category", category=category)

        count = qs.count()
        qs.delete()
        messages.success(request, f'Acesso removido de "{name}" ({count} usuário(s) apagado(s)).')
        return redirect("gestao_category", category=category)

    # clinicas / veterinarios
    Model = Clinic if category == "clinicas" else Veterinarian
    obj = get_object_or_404(Model, pk=pk)
    name = getattr(obj, "display_name", None) or getattr(obj, "name", "Usuário")

    u = getattr(obj, "user", None)
    if not u:
        messages.info(request, f'"{name}" não possui conta ativa.')
        return redirect("gestao_category", category=category)

    # desassocia do objeto e tira dos exames
    obj.user = None
    obj.save(update_fields=["user"])
    try:
        Exam.objects.filter(assigned_user=u).update(assigned_user=None)
    except Exception:
        pass

    try:
        u.delete()
    except Exception as e:
        messages.error(request, f"Não foi possível apagar o usuário: {e}")
        return redirect("gestao_category", category=category)

    messages.success(request, f'Acesso removido de "{name}".')
    return redirect("gestao_category", category=category)
    
@login_required
@admin_required
def management_delete(request, category, pk):
    if request.method != "POST":
        return redirect('gestao_category', category=category)

    category_map = {
        'tutores': Tutor,
        'clinicas': Clinic,
        'veterinarios': Veterinarian,
        'pets': Pet,
    }

    Model = category_map.get(category)
    if not Model:
        messages.error(request, "Categoria inválida.")
        return redirect('gestao')

    obj = get_object_or_404(Model, pk=pk)
    name = getattr(obj, "name", "item")

    # Se for clínica/vet, pode ter user associado
    linked_user = getattr(obj, "user", None)
    
    # Se for tutor, não tem FK de user -> procurar pelo email (role TUTOR)
    tutor_users_qs = None
    if category == "tutores":
        email = (getattr(obj, "email", "") or "").strip()
        if email:
            tutor_users_qs = User.objects.filter(
                email__iexact=email,
                profile__role="TUTOR"
            )

    try:
        obj.delete()
    except ProtectedError:
        messages.error(
            request,
            "Não foi possível excluir porque este item está sendo usado por outros registros."
        )
        return redirect('gestao_category', category=category)

    # Se tinha login, apaga o usuário (perde acesso)
    if linked_user:
        if category in ("clinicas", "veterinarios"):
            try:
                Exam.objects.filter(assigned_user=linked_user).update(assigned_user=None)
            except Exception:
                pass

        try:
            linked_user.delete()
        except Exception:
            pass
    
    # Se for tutor, não tem FK de user -> procurar pelo email (role TUTOR)
    tutor_users_qs = None
    if category == "tutores":
        email = (getattr(obj, "email", "") or "").strip()
        if email:
            tutor_users_qs = User.objects.filter(
                email__iexact=email,
                profile__role="TUTOR"
            )

    messages.success(request, f'"{name}" foi excluído com sucesso.')
    return redirect('gestao_category', category=category)
    
@login_required
@superadmin_required
def admin_user_create(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = AdminAuxForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            first = (cd.get("first_name") or "").strip()
            last  = (cd.get("last_name") or "").strip()
            email = (cd.get("email") or "").strip()
            phone = (cd.get("phone") or "").strip()
            photo = cd.get("photo")

            base = _to_login_base(email.split("@")[0] if email else first)
            username = _make_unique_username(base)

            u = User(username=username, first_name=first, last_name=last, email=email)
            u.set_unusable_password()
            u.save()

            p, _ = Profile.objects.get_or_create(user=u)
            p.role = "ADMIN_AUX"
            p.whatsapp = phone
            if photo:
                p.photo = photo
            p.save()
            
            notify_email = (cd.get("notify_email") or "1") != "0"
            notify_phone = (cd.get("notify_phone") or "1") != "0"

            if not email:
                notify_email = False
                notify_phone = False

            if not is_whatsapp_phone(phone):
                notify_phone = False

            activation_link = build_activation_link(request, u)
            recipient_label = _notification_label(first, "admin")

            if notify_email:
                try:
                    send_portal_access_email(
                        request,
                        to_email=email,
                        recipient_label=recipient_label,
                        activation_link=activation_link,
                        resend=False,
                    )
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail de cadastro: {e}")

            if notify_phone:
                try:
                    send_portal_access_whatsapp(
                        request,
                        to_phone=phone,
                        recipient_label=recipient_label,
                        activation_link=activation_link,
                        resend=False,
                    )
                except Exception as e:
                    messages.error(request, f"Falha ao enviar WhatsApp de cadastro: {e}")

            messages.success(request, "Auxiliar cadastrado com sucesso.")
            return redirect("gestao_category", category="admin")
    else:
        form = AdminAuxForm()

    return render(request, "accounts/management_form.html", {
        "profile": profile,
        "form": form,
        "category": "admin",
        "category_singular": "Auxiliar",
        "is_edit": False,
        "obj": None,  # sem foto prévia no create
    })
    
@login_required
@superadmin_required
def admin_user_edit(request, user_id):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    target_user = get_object_or_404(User, pk=user_id)
    target_profile, _ = Profile.objects.get_or_create(user=target_user)
    
    old_email = (target_user.email or "").strip()
    old_phone = (target_profile.whatsapp or "").strip()
    
    if target_user.is_superuser or target_profile.role == 'ADMIN':
        messages.error(request, "Não é permitido editar um Administrador por aqui.")
        return redirect('gestao_category', category='admin')

    if target_profile.role not in ("ADMIN", "ADMIN_AUX"):
        return HttpResponseForbidden("Acesso negado.")

    singular = "Administrador" if target_profile.role == "ADMIN" else "Auxiliar"

    if request.method == "POST":
        form = AdminAuxForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data

            target_user.first_name = (cd.get("first_name") or "").strip()
            target_user.last_name  = (cd.get("last_name") or "").strip()
            target_user.email      = (cd.get("email") or "").strip()
            target_user.save()

            target_profile.whatsapp = (cd.get("phone") or "").strip()

            # remover foto (vem do hidden remove_photo do template)
            if (request.POST.get("remove_photo") or "0") == "1":
                if target_profile.photo:
                    target_profile.photo.delete(save=False)
                target_profile.photo = None

            # trocar foto (se enviou uma nova)
            if cd.get("photo"):
                target_profile.photo = cd["photo"]

            target_profile.save()
            
            new_email = (target_user.email or "").strip()
            new_phone = (target_profile.whatsapp or "").strip()

            notify_email = (cd.get("notify_email") or "1") != "0"
            notify_phone = (cd.get("notify_phone") or "1") != "0"

            email_changed = (new_email.lower() != old_email.lower()) if (new_email and old_email) else (new_email != old_email)
            phone_changed = _phone_digits(new_phone) != _phone_digits(old_phone)
            contacts_changed = email_changed or phone_changed

            recipient_label = _notification_label(target_user.first_name or target_user.username, "admin")

            if contacts_changed:
                if notify_email and new_email:
                    try:
                        send_contact_updated_email(
                            request,
                            to_email=new_email,
                            recipient_label=recipient_label,
                            email_value=new_email,
                            phone_value=new_phone,
                        )
                    except Exception as e:
                        messages.error(request, f"Falha ao enviar e-mail de atualização: {e}")

                if notify_phone and new_phone and is_whatsapp_phone(new_phone):
                    try:
                        ok = send_contact_updated_whatsapp(
                            request,
                            to_phone=new_phone,
                            recipient_label=recipient_label,
                            email_value=new_email,
                            phone_value=new_phone,
                        )
                        if not ok:
                            messages.warning(request, "O WhatsApp de atualização não foi enviado.")
                    except Exception as e:
                        messages.error(request, f"Falha ao enviar WhatsApp de atualização: {e}")

            messages.success(request, f"{singular} atualizado com sucesso.")
            return redirect("gestao_category", category="admin")
    else:
        form = AdminAuxForm(initial={
            "first_name": target_user.first_name,
            "last_name": target_user.last_name,
            "email": target_user.email,
            "phone": target_profile.whatsapp,
            "notify_phone": "0",
            "notify_email": "0",
        })

    return render(request, "accounts/management_form.html", {
        "profile": profile,
        "form": form,
        "category": "admin",
        "category_singular": singular,
        "is_edit": True,
        "obj": target_profile,  # aqui a preview mostra a foto atual
    })
    
@login_required
@superadmin_required
def admin_user_delete(request, user_id: int):
    if request.method != 'POST':
        return redirect('gestao_category', category='admin')

    target_user = get_object_or_404(User, pk=user_id)
    target_profile, _ = Profile.objects.get_or_create(user=target_user)

    if target_user.is_superuser or target_profile.role == 'ADMIN':
        messages.error(request, "Não é permitido excluir um Administrador por aqui.")
        return redirect('gestao_category', category='admin')

    if target_profile.role != 'ADMIN_AUX':
        messages.error(request, "Este usuário não é um Administrador Auxiliar.")
        return redirect('gestao_category', category='admin')

    Clinic.objects.filter(user=target_user).update(user=None)
    Veterinarian.objects.filter(user=target_user).update(user=None)
    Exam.objects.filter(assigned_user=target_user).update(assigned_user=None)

    name = target_user.first_name or target_user.username
    target_user.delete()
    messages.success(request, f'"{name}" foi excluído com sucesso.')
    return redirect('gestao_category', category='admin')
    
@login_required
@superadmin_required
def admin_user_resend_alerts(request, user_id):
    if request.method != "POST":
        return redirect('gestao_category', category='admin')

    target_user = get_object_or_404(User, pk=user_id)
    target_profile, _ = Profile.objects.get_or_create(user=target_user)

    if target_profile.role != 'ADMIN_AUX':
        messages.error(request, "Só é possível reenviar alertas para auxiliares.")
        return redirect('gestao_category', category='admin')

    if target_user.has_usable_password():
        messages.info(request, "Este auxiliar já possui conta ativa.")
        return redirect('gestao_category', category='admin')

    email = (target_user.email or '').strip()
    phone = (target_profile.whatsapp or '').strip()
    name = (target_user.first_name or target_user.username)

    # por enquanto: envio real só por e-mail
    if not email:
        if is_whatsapp_phone(phone):
            messages.info(
                request,
                "Este auxiliar só possui telefone WhatsApp. O reenvio por WhatsApp ainda não está habilitado."
            )
        else:
            messages.error(
                request,
                "Não é possível reenviar alertas porque o auxiliar não possui e-mail cadastrado."
            )
        return redirect('gestao_category', category='admin')

    try:
        validate_email(email)
    except DjangoValidationError:
        messages.error(request, "O e-mail cadastrado deste auxiliar é inválido.")
        return redirect('gestao_category', category='admin')

    activation_link = build_activation_link(request, target_user)
    recipient_label = _notification_label(name, "admin")

    try:
        if email:
            send_portal_access_email(
                request,
                to_email=email,
                recipient_label=recipient_label,
                activation_link=activation_link,
                resend=True,
            )

        if is_whatsapp_phone(phone):
            ok = send_portal_access_whatsapp(
                request,
                to_phone=phone,
                recipient_label=recipient_label,
                activation_link=activation_link,
                resend=True,
            )
                
            if not ok:
                messages.warning(request, "O WhatsApp de reenvio não foi enviado.")

        messages.success(request, f'Alertas reenviados para "{name}".')
    except Exception as e:
        messages.error(request, f"Falha ao reenviar alertas: {e}")

    return redirect('gestao_category', category='admin')
    
def activate_account(request, uidb64, token):
    User = get_user_model()

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return render(request, "accounts/activate_account_invalid.html")

    if request.method == "POST":
        form = SetPasswordForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()  # salva a nova senha
            login(request, user)  # já loga automaticamente
            return redirect("exames")  # ajustar se o nome for outro
    else:
        form = SetPasswordForm(user=user)

    return render(request, "accounts/activate_account.html", {"form": form})
    
@login_required
@admin_required
def exam_types_list(request):
    q = (request.GET.get("q") or "").strip()
    order = request.GET.get("order") or "abbreviation"
    direction = request.GET.get("direction") or "asc"

    items = ExamTypeAlias.objects.all()

    if q:
        items = items.filter(
            Q(abbreviation__icontains=q) |
            Q(full_name__icontains=q)
        )

    allowed_orders = {
        "sigla": "abbreviation",
        "exame": "full_name",
    }
    order_field = allowed_orders.get(order, "abbreviation")
    if direction == "desc":
        order_field = "-" + order_field
    items = items.order_by(order_field)

    profile, _ = Profile.objects.get_or_create(user=request.user)

    return render(request, "accounts/exam_types_list.html", {
        "profile": profile,
        "items": items,
        "search_query": q,
        "order": order,
        "direction": direction,
    })


@login_required
@admin_required
def exam_types_create(request):
    if request.method == "POST":
        form = ExamTypeAliasForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Sigla de exame cadastrada com sucesso.")
            return redirect("exam_types")
    else:
        form = ExamTypeAliasForm()

    profile, _ = Profile.objects.get_or_create(user=request.user)

    return render(request, "accounts/exam_types_form.html", {
        "profile": profile,
        "form": form,
        "title": "Cadastrar sigla de exame",
    })
    
@login_required
@admin_required
def exam_types_delete(request, pk):
    if request.method != "POST":
        return redirect("exam_types")

    obj = get_object_or_404(ExamTypeAlias, pk=pk)
    obj.delete()
    messages.success(request, "Sigla excluída com sucesso.")
    return redirect("exam_types")

def logout_view(request):
    logout(request)
    return redirect('login')

