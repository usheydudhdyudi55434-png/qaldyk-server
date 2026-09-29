
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import SensorReading, Container


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
            "status": reading.status
        }, status=201)


def dashboard(request):

    reading = SensorReading.objects.order_by('-created_at').first()

    return render(request, 'dashboard.html', {
        'reading': reading
    })
