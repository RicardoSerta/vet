from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.utils.encoding import force_str, force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils import timezone
from django.conf import settings
from .notifications import send_exam_email
from django.contrib import messages
from django.db.models import Q
from django.urls import reverse
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from .authz import admin_required, is_admin_user, is_superadmin_user, superadmin_required
import os
import mimetypes
import re
import unicodedata
from .models import Profile, Exam, Tutor, Clinic, Veterinarian, Pet, ExamTypeAlias, ExamExtraPDF
from .forms import ExamUploadForm, TutorForm, ClinicForm, VeterinarianForm, PetForm, MultiExamUploadForm, parse_exam_filename, ExamTypeAliasForm

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
        user_email = (user.email or "").strip().lower()
        exam_email = (exam.tutor_email or "").strip().lower()
        return bool(user_email) and (user_email == exam_email)

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
            new_email = request.user.email or ""
            if old_email.strip() and new_email.strip() and old_email.strip().lower() != new_email.strip().lower():
                Exam.objects.filter(tutor_email__iexact=old_email.strip()).update(tutor_email=new_email.strip())

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

    if not is_admin_user(request.user):
        if profile.role == "TUTOR":
            # Tutor vê exames onde o tutor_email do exame é o email do usuário
            exams = exams.filter(tutor_email__iexact=(request.user.email or ""))
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
        'retorno': 'retorno_previsto',
    }

    if order in order_map:
        field_name = order_map[order]
        if direction == 'desc':
            field_name = '-' + field_name
        exams = exams.order_by(field_name)

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
            
            selected = cd['clinic_or_vet']
            assigned_user = None
            links_to_show = []
            provider_activation_link = None
            tutor_activation_link = None
            
            if selected.startswith("CLINIC:"):
                clinic_id = int(selected.split(":")[1])
                clinic = Clinic.objects.get(id=clinic_id)
                clinic_or_vet_name = clinic.name

                # garante user pendente se tiver contato
                if clinic.user is None:
                    u, created_now, needs_activation = ensure_pending_user_for_provider(
                        name=clinic.name,
                        email=clinic.email,
                        phone=clinic.phone,
                        role="BASIC",
                    )
                    if u:
                        clinic.user = u
                        clinic.save(update_fields=["user"])
                        assigned_user = u

                        if needs_activation:
                            provider_activation_link = build_activation_link(request, u)
                    else:
                        assigned_user = None
                else:
                    assigned_user = clinic.user
                    # se já existe user mas ainda não ativou
                    if not assigned_user.has_usable_password():
                        provider_activation_link = build_activation_link(request, assigned_user)
                        
            elif selected.startswith("VET:"):
                vet_id = int(selected.split(":")[1])
                vet = Veterinarian.objects.get(id=vet_id)
                clinic_or_vet_name = vet.name

                if vet.user is None:
                    u, created_now, needs_activation = ensure_pending_user_for_provider(
                        name=vet.name,
                        email=vet.email,
                        phone=vet.phone,
                        role="BASIC",
                    )
                    if u:
                        vet.user = u
                        vet.save(update_fields=["user"])
                        assigned_user = u

                        if needs_activation:
                            provider_activation_link = build_activation_link(request, u)
                    else:
                        assigned_user = None
                else:
                    assigned_user = vet.user
                    if not assigned_user.has_usable_password():
                        provider_activation_link = build_activation_link(request, assigned_user)
                
            else:
                clinic_or_vet_name = ""
                assigned_user = None
                
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

            if tutor_email:
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
                observations=cd['observations'],
                pdf_file=cd['pdf_file'],
                owner=request.user,
                assigned_user=assigned_user,
                additional_clinic_or_vet=cd.get("additional_clinic_or_vet") or [],
            )
            
            sent_any = False

            # 1) Tutor (se preencheu e-mail)
            if tutor_email:
                try:
                    ok = send_exam_email(
                        request,
                        exam=exam,
                        to_email=tutor_email,
                        recipient_label=exam.tutor_name,
                        activation_link=tutor_activation_link,
                    )
                    sent_any = sent_any or ok
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail para o tutor: {e}")

            # 2) Clínica/Vet selecionado (se tiver e-mail cadastrado)
            provider_email = ""
            if selected.startswith("CLINIC:"):
                provider_email = (clinic.email or "").strip()
                provider_label = clinic.name
            elif selected.startswith("VET:"):
                provider_email = (vet.email or "").strip()
                provider_label = vet.name
            else:
                provider_label = "Clínica/Veterinário"

            if provider_email:
                try:
                    ok = send_exam_email(
                        request,
                        exam=exam,
                        to_email=provider_email,
                        recipient_label=provider_label,
                        activation_link=provider_activation_link,
                    )
                    sent_any = sent_any or ok
                except Exception as e:
                    messages.error(request, f"Falha ao enviar e-mail para a clínica/vet: {e}")

            # Se enviou pelo menos 1 e-mail, marca a coluna Alerta Email
            if sent_any:
                exam.alerta_email = timezone.now()
                exam.save(update_fields=["alerta_email"])

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

            assigned_user = None
            clinic_or_vet_name = ""

            if selected.startswith("CLINIC:"):
                clinic_id = int(selected.split(":")[1])
                clinic = Clinic.objects.get(id=clinic_id)
                clinic_or_vet_name = clinic.name
                assigned_user = clinic.user
            elif selected.startswith("VET:"):
                vet_id = int(selected.split(":")[1])
                vet = Veterinarian.objects.get(id=vet_id)
                clinic_or_vet_name = vet.name
                assigned_user = vet.user

            # cria todos em transação (ou cria tudo, ou cria nada)
            created_count = 0
            with transaction.atomic():
                for f in pdf_files:
                    data = parse_exam_filename(f.name)
                    
                    ensure_tutor_and_pet(
                        tutor_name=data["tutor_name"],
                        pet_name=data["pet_name"],
                        breed=data["breed"],
                    )

                    
                    Exam.objects.create(
                        date_realizacao=data["date_realizacao"],
                        clinic_or_vet=clinic_or_vet_name,
                        exam_type=translate_exam_type(data["exam_type"]),
                        pet_name=data["pet_name"],
                        breed=data["breed"],
                        tutor_name=data["tutor_name"],
                        pdf_file=f,
                        owner=request.user,
                        assigned_user=assigned_user,
                        # campos opcionais vazios no upload em massa:
                        tutor_phone="",
                        tutor_email="",
                        observations="",
                    )
                    created_count += 1

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

    return render(request, "accounts/exam_view.html", {
        "profile": profile,
        "exam": exam,
        "extras": extras,
        "is_admin": is_admin_user(request.user),
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
        
    if category == 'admin':
        if not is_superadmin:
            messages.error(request, "Você não tem permissão para acessar essa aba.")
            return redirect('gestao')

        qs = Profile.objects.select_related('user').filter(role__in=['ADMIN', 'ADMIN_AUX'])

        search_query = request.GET.get('q', '').strip()
        if search_query:
            qs = qs.filter(
                Q(user__first_name__icontains=search_query) |
                Q(user__username__icontains=search_query) |
                Q(user__email__icontains=search_query) |
                Q(whatsapp__icontains=search_query)
            )

        order = request.GET.get('order', '')
        direction = request.GET.get('direction', 'asc')
        order_map = {
            'nome': 'user__first_name',
            'email': 'user__email',
            'telefone': 'whatsapp',
            'data': 'user__date_joined',
            'funcao': 'role',
        }

        if order in order_map:
            field_name = order_map[order]
            if direction == 'desc':
                field_name = '-' + field_name
            qs = qs.order_by(field_name)
        else:
            qs = qs.order_by('-user__date_joined')

        items = []
        for p in qs:
            role_label = 'Administrador' if p.role == 'ADMIN' else 'Auxiliar'
            items.append({
                'id': p.user_id,
                'name': (p.user.first_name or p.user.username),
                'email': (p.user.email or ''),
                'phone': (p.whatsapp or ''),
                'created_at': p.user.date_joined,
                'role_label': role_label,
                'can_delete': (p.role == 'ADMIN_AUX') and (not p.user.is_superuser),
            })

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
        from django.db.models import Q
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

    categories_nav = [
        {'slug': key, 'label': value['label']}
        for key, value in MANAGEMENT_CATEGORIES.items()
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

    info = MANAGEMENT_CATEGORIES[category]
    FormClass = info['form']

    if request.method == 'POST':
        form = FormClass(request.POST)
        if form.is_valid():
            form.save()
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

    if request.method == "POST":
        form = info['form'](request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()

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
        # evita apagar exames por acidente (caso seu FK esteja como CASCADE)
        try:
            Exam.objects.filter(assigned_user=linked_user).update(assigned_user=None)
        except Exception:
            pass

        try:
            linked_user.delete()
        except Exception:
            pass

    messages.success(request, f'"{name}" foi excluído com sucesso.')
    return redirect('gestao_category', category=category)
    
@login_required
@superadmin_required
def admin_user_delete(request, user_id: int):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    target_user = get_object_or_404(User, pk=user_id)
    target_profile, _ = Profile.objects.get_or_create(user=target_user)

    if target_user.is_superuser or target_profile.role == 'ADMIN':
        messages.error(request, "Não é permitido excluir um Administrador por aqui.")
        return redirect('gestao_category', category='admin')

    if target_profile.role != 'ADMIN_AUX':
        messages.error(request, "Este usuário não é um Administrador Auxiliar.")
        return redirect('gestao_category', category='admin')

    if request.method == 'POST':
        # segurança extra (caso esteja vinculado a algo por acidente)
        Clinic.objects.filter(user=target_user).update(user=None)
        Veterinarian.objects.filter(user=target_user).update(user=None)
        Exam.objects.filter(assigned_user=target_user).update(assigned_user=None)

        name = target_user.first_name or target_user.username
        target_user.delete()
        messages.success(request, f'"{name}" foi excluído com sucesso.')
        return redirect('gestao_category', category='admin')

    return render(request, 'accounts/admin_confirm_delete.html', {
        'profile': profile,
        'target_user': target_user,
        'target_profile': target_profile,
    })
    
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

