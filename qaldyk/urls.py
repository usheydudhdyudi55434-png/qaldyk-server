from django.contrib import admin
from django.urls import path, include
from sensor.views import dashboard


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('sensor.urls')),
    path('dashboard/', dashboard, name='dashboard'),
]