from django.urls import path
from . import views

app_name = "chatbot"

urlpatterns = [
    path('stream_chat/', views.stream_chat, name='stream_chat'),
]
