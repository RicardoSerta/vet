from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from .models import Profile


def is_admin_user(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role in ('ADMIN', 'ADMIN_AUX')
    except Profile.DoesNotExist:
        return False
        
def superadmin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_super_admin_user(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, "Você não tem permissão para acessar essa área.")
        return redirect('meu_perfil')
    return wrapper

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if is_admin_user(request.user):
            return view_func(request, *args, **kwargs)
        messages.error(request, "Você não tem permissão para acessar essa área.")
        return redirect('meu_perfil')
    return wrapper

