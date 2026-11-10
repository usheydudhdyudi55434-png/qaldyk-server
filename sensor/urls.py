
from django.urls import path

from .live_views import workers_live

from .views import (
    containers_history,
    containers_overview,
    dashboard,
    districts_geojson,
    sensor_data,
)


urlpatterns = [
    path('sensor/<int:container_id>/', sensor_data),
    path('containers/', containers_overview),
    path('containers/history/', containers_history),
    path('districts/', districts_geojson),
    path('workers/', workers_live),
    path('dashboard/', dashboard, name='dashboard'),
]

