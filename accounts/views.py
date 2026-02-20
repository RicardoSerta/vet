from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.contrib import messages
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseForbidden
from .authz import admin_required
from .authz import is_admin_user
import os
import mimetypes
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
    
def translate_exam_type(exam_type_raw: str) -> str:
    key = (exam_type_raw or "").strip().lower()
    if not key:
        return exam_type_raw
    alias = ExamTypeAlias.objects.filter(abbreviation=key).first()
    return alias.full_name if alias else exam_type_raw

def login_view(request):
    if request.user.is_authenticated:
        return redirect('meu_perfil')

    error = None

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('meu_perfil')
        else:
            error = 'Login ou senha inválidos.'

    return render(request, 'accounts/login.html', {'error': error})


@login_required
def profile_view(request):
    # Garante que exista um Profile para o usuário logado
    profile, created = Profile.objects.get_or_create(user=request.user)

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

    if not is_admin_user(request.user) and exam.assigned_user_id != request.user.id:
        raise Http404()

    if not exam.pdf_file:
        raise Http404()

    return FileResponse(exam.pdf_file.open('rb'), content_type='application/pdf')
    
@login_required
def exams_list(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    exams = Exam.objects.all()
    
    if not is_admin_user(request.user):
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
    
    
    if not is_admin_user(request.user) and exam.assigned_user_id != request.user.id:
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

            if selected.startswith("CLINIC:"):
                clinic_id = int(selected.split(":")[1])
                clinic = Clinic.objects.get(id=clinic_id)
                clinic_or_vet_name = clinic.name
                assigned_user = clinic.user  # pode ser None se alguma clínica antiga não tiver user
            elif selected.startswith("VET:"):
                vet_id = int(selected.split(":")[1])
                vet = Veterinarian.objects.get(id=vet_id)
                clinic_or_vet_name = vet.name
                assigned_user = vet.user
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
            )

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

    if not is_admin_user(request.user) and exam.assigned_user_id != request.user.id:
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

    if not is_admin_user(request.user) and exam.assigned_user_id != request.user.id:
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

    if category not in MANAGEMENT_CATEGORIES:
        category = 'tutores'

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

