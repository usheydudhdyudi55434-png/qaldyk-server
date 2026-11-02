
import os
from datetime import timedelta

from django.contrib.staticfiles import finders
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import SensorReading, Container


HISTORY_HOURS = (1, 6, 24, 168)
HISTORY_POINTS = 120


@csrf_exempt
@api_view(['GET', 'POST'])
def sensor_data(request, container_id):

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

        reading = SensorReading.objects.create(
            container=container,
            distance=distance,
            fill_level=fill_level,
            status=status
        )

        return Response({
            "message": "Sensor data saved!",
            "id": reading.id,
            "container": reading.container.name,
            "distance": reading.distance,
            "fill_level": reading.fill_level,
            "status": reading.status,
            "latitude": container.latitude,
            "longitude": container.longitude
        }, status=201)


@api_view(['GET'])
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
        "containers": containers
    })


@api_view(['GET'])
def containers_history(request):

    try:
        hours = int(request.query_params.get('hours', 24))
    except ValueError:
        hours = None

    if hours not in HISTORY_HOURS:
        return Response({
            "error": f"hours must be one of {HISTORY_HOURS}"
        }, status=400)

    bucket_seconds = hours * 3600 // HISTORY_POINTS
    since = timezone.now() - timedelta(hours=hours)

    readings = SensorReading.objects.filter(
        created_at__gte=since,
        container__isnull=False
    ).values_list('container_id', 'created_at', 'fill_level')

    buckets = {}

    for container_id, created_at, fill_level in readings.iterator():
        bucket = int(created_at.timestamp()) // bucket_seconds * bucket_seconds
        total, count = buckets.get((container_id, bucket), (0, 0))
        buckets[(container_id, bucket)] = (total + fill_level, count + 1)

    series = {}

    for (container_id, bucket), (total, count) in sorted(buckets.items()):
        series.setdefault(container_id, []).append(
            [bucket * 1000, round(total / count, 2)]
        )

    return Response({
        "hours": hours,
        "bucket_seconds": bucket_seconds,
        "server_time": timezone.now(),
        "series": series
    })


def asset_version(path):

    # Changes with the file, so browsers never keep running an old copy
    return int(os.path.getmtime(finders.find(path)))


def dashboard(request):

    reading = SensorReading.objects.order_by('-created_at').first()

    return render(request, 'dashboard.html', {
        'reading': reading,
        'css_version': asset_version('sensor/css/dashboard.css'),
        'js_version': asset_version('sensor/js/dashboard.js')
    })
