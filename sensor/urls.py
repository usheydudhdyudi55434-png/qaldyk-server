
from django.urls import path

from .views import sensor_data, dashboard


urlpatterns = [
    path('sensor/<int:container_id>/', sensor_data),
    path('dashboard/', dashboard, name='dashboard'),
]

