from .authz import is_admin_user

def user_roles(request):
    return {
        "is_admin": is_admin_user(request.user)
    }

