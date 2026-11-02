from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, include
from sensor.views import dashboard, DispatcherLoginView


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('sensor.urls')),
    path('login/', DispatcherLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('dashboard/', dashboard, name='dashboard'),
]
