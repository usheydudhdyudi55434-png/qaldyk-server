
import os
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.contrib.staticfiles import finders
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import fire
from .models import SensorReading, Container


HISTORY_HOURS = (1, 6, 24, 168)
HISTORY_POINTS = 120

# Fill level is averaged; heat and gas keep the peak so a short spike is not lost
HISTORY_METRICS = {
    "fill": ("fill_level", "avg"),
    "temperature": ("temperature", "max"),
    "gas": ("gas_level", "max"),
}


@csrf_exempt
@api_view(['GET', 'POST'])
def sensor_data(request, container_id):

    # Devices POST without a session; reading the data needs a login
    if request.method == 'GET' and not request.user.is_authenticated:
        return Response({
            "error": "Authentication required"
        }, status=403)

    try:
        container = Container.objects.get(id=container_id)
    except Container.DoesNotExist:
        return Response({
            "error": "Container not found"
        }, status=404)

    if request.method == 'GET':

        readings = SensorReading.objects.filter(
            container=container
        ).order_by('-created_at')

        data = []

        for reading in readings:
            data.append({
                "id": reading.id,
                "container": reading.container.name,
                "distance": reading.distance,
                "fill_level": reading.fill_level,
                "status": reading.status,
                "created_at": reading.created_at
            })

        return Response(data)

    if request.method == 'POST':

        distance = request.data.get('distance')
        fill_level = request.data.get('fill_level')

        if distance is None or fill_level is None:
            return Response({
                "error": "distance and fill_level are required"
            }, status=400)

        distance = float(distance)
        fill_level = float(fill_level)

        temperature = request.data.get('temperature')
        gas_level = request.data.get('gas_level')
        device_risk = request.data.get('fire_risk')

        try:
            if temperature is not None:
                temperature = float(temperature)
                if not -50 <= temperature <= 150:
                    raise ValueError
            if gas_level is not None:
                gas_level = int(gas_level)
                if not 0 <= gas_level <= fire.GAS_MAX:
                    raise ValueError
            if device_risk is not None:
                device_risk = str(device_risk).upper()
                if device_risk not in fire.RISK_LEVELS:
                    raise ValueError
        except (TypeError, ValueError):
            return Response({
                "error": (
                    "temperature must be -50..150, "
                    f"gas_level 0..{fire.GAS_MAX}, "
                    f"fire_risk one of {fire.RISK_LEVELS}"
                )
            }, status=400)

        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')

        if latitude is not None or longitude is not None:
            try:
                latitude = float(latitude)
                longitude = float(longitude)
            except (TypeError, ValueError):
                latitude = longitude = float('nan')

            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                return Response({
                    "error": "latitude and longitude must be a valid pair"
                }, status=400)

            if (latitude, longitude) != (container.latitude, container.longitude):
                container.latitude = latitude
                container.longitude = longitude
                container.save(update_fields=['latitude', 'longitude'])

        if fill_level >= 80:
            status = "FULL"
        elif fill_level >= 50:
            status = "HALF FULL"
        else:
            status = "NORMAL"

        previous = container.readings.order_by('-created_at').values_list(
            'temperature', 'created_at'
        ).first()

        fire_risk, fire_note = fire.assess(
            temperature,
            gas_level,
            device_risk=device_risk,
            previous=previous,
            now=timezone.now()
        )

        reading = SensorReading.objects.create(
            container=container,
            distance=distance,
            fill_level=fill_level,
            status=status,
            temperature=temperature,
            gas_level=gas_level,
            fire_risk=fire_risk,
            fire_note=fire_note
        )

        return Response({
            "message": "Sensor data saved!",
            "id": reading.id,
            "container": reading.container.name,
            "distance": reading.distance,
            "fill_level": reading.fill_level,
            "status": reading.status,
            "temperature": reading.temperature,
            "gas_level": reading.gas_level,
            "fire_risk": reading.fire_risk,
            "fire_note": reading.fire_note,
            "latitude": container.latitude,
            "longitude": container.longitude
        }, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def containers_overview(request):

    containers = []

    for container in Container.objects.order_by('id'):
        reading = container.readings.order_by('-created_at').first()

        latest = None

        if reading:
            latest = {
                "distance": reading.distance,
                "fill_level": reading.fill_level,
                "status": reading.status,
                "temperature": reading.temperature,
                "gas_level": reading.gas_level,
                "fire_risk": reading.fire_risk,
                "fire_note": reading.fire_note,
                "created_at": reading.created_at
            }

        containers.append({
            "id": container.id,
            "name": container.name,
            "location": container.location,
            "latitude": container.latitude,
            "longitude": container.longitude,
            "latest": latest
        })

    return Response({
        "server_time": timezone.now(),
        "fire_thresholds": fire.thresholds(),
        "containers": containers
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def containers_history(request):

    try:
        hours = int(request.query_params.get('hours', 24))
    except ValueError:
        hours = None

    if hours not in HISTORY_HOURS:
        return Response({
            "error": f"hours must be one of {HISTORY_HOURS}"
        }, status=400)

    metric = request.query_params.get('metric', 'fill')

    if metric not in HISTORY_METRICS:
        return Response({
            "error": f"metric must be one of {tuple(HISTORY_METRICS)}"
        }, status=400)

    field, aggregate = HISTORY_METRICS[metric]

    bucket_seconds = hours * 3600 // HISTORY_POINTS
    since = timezone.now() - timedelta(hours=hours)

    readings = SensorReading.objects.filter(
        created_at__gte=since,
        container__isnull=False,
        **{f"{field}__isnull": False}
    ).values_list('container_id', 'created_at', field)

    buckets = {}

    for container_id, created_at, value in readings.iterator():
        bucket = int(created_at.timestamp()) // bucket_seconds * bucket_seconds
        total, count, peak = buckets.get((container_id, bucket), (0, 0, value))
        buckets[(container_id, bucket)] = (total + value, count + 1, max(peak, value))

    series = {}

    for (container_id, bucket), (total, count, peak) in sorted(buckets.items()):
        value = total / count if aggregate == "avg" else peak
        series.setdefault(container_id, []).append(
            [bucket * 1000, round(value, 2)]
        )

    return Response({
        "hours": hours,
        "metric": metric,
        "bucket_seconds": bucket_seconds,
        "server_time": timezone.now(),
        "series": series
    })


def asset_version(path):

    # Changes with the file, so browsers never keep running an old copy
    return int(os.path.getmtime(finders.find(path)))


class DispatcherLoginForm(AuthenticationForm):

    error_messages = {
        "invalid_login": "Логин немесе құпия сөз қате.",
        "inactive": "Бұл аккаунт өшірілген."
    }


class DispatcherLoginView(LoginView):

    template_name = 'login.html'
    authentication_form = DispatcherLoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            css_version=asset_version('sensor/css/dashboard.css'),
            **kwargs
        )


@login_required
def dashboard(request):

    reading = SensorReading.objects.order_by('-created_at').first()

    return render(request, 'dashboard.html', {
        'reading': reading,
        'css_version': asset_version('sensor/css/dashboard.css'),
        'js_version': asset_version('sensor/js/dashboard.js')
    })
