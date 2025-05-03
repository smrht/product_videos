from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('task-status/<uuid:task_id>/', views.task_status_view, name='task_status'),
    path('status/<uuid:video_generation_id>/', views.check_video_status, name='check_video_status'),
]
