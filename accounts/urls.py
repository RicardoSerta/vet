from django.urls import path
from django.shortcuts import redirect
from . import views

urlpatterns = [
    path('', lambda request: redirect('login'), name='home'),
    path('login/', views.login_view, name='login'),
    path('meu-perfil/', views.profile_view, name='meu_perfil'),
    path('logout/', views.logout_view, name='logout'),

    path('exames/', views.exams_list, name='exames'),
    path('exames/novo/', views.exam_upload, name='exam_upload'),
    path('exames/<int:pk>/', views.exam_detail, name='exam_detail'),
    path('exames/<int:pk>/excluir/', views.exam_delete, name='exam_delete'),
    path('exames/<int:pk>/encaminhar/', views.exam_forward, name='exam_forward'),

    path('gestao/', views.management_view, name='gestao'),
    path('gestao/<str:category>/', views.management_view, name='gestao_category'),
    path('gestao/<str:category>/novo/', views.management_create, name='gestao_create'),
    path('gestao/<str:category>/<int:pk>/editar/', views.management_edit, name='gestao_edit'),
    path('gestao/<str:category>/<int:pk>/excluir/', views.management_delete, name='gestao_delete'),
    path('exames/<int:pk>/pdf/', views.exam_pdf, name='exam_pdf'),
    path('exames/novo-multiplo/', views.exam_upload_multi, name='exam_upload_multi'),
    path("exames/tipos/", views.exam_types_list, name="exam_types"),
    path("exames/tipos/novo/", views.exam_types_create, name="exam_types_create"),
    path("exames/tipos/<int:pk>/excluir/", views.exam_types_delete, name="exam_types_delete"),
    path("exames/<int:pk>/ver/", views.exam_view, name="exam_view"),
    path("exames/<int:pk>/extras/<int:extra_pk>/pdf/", views.exam_extra_pdf, name="exam_extra_pdf"),
    path("ativar/<uidb64>/<token>/", views.activate_account, name="activate_account"),
]

