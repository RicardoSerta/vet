from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .models import Profile


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


def logout_view(request):
    logout(request)
    return redirect('login')

