from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('meu-perfil/', views.profile_view, name='meu_perfil'),
    path('logout/', views.logout_view, name='logout'),

    path('exames/', views.exams_list, name='exames'),
    path('exames/<int:pk>/', views.exam_detail, name='exam_detail'),
    path('exames/<int:pk>/excluir/', views.exam_delete, name='exam_delete'),
    path('exames/<int:pk>/encaminhar/', views.exam_forward, name='exam_forward'),
]


