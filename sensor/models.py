from django.db import models


class Container(models.Model):
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class SensorReading(models.Model):
    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name="readings",
        null=True,
        blank=True
    )

    distance = models.FloatField()
    fill_level = models.FloatField()
    status = models.CharField(max_length=20)

    # Fire risk signs; empty on devices without temperature or gas sensors
    temperature = models.FloatField(null=True, blank=True)
    gas_level = models.IntegerField(null=True, blank=True)
    fire_risk = models.CharField(max_length=10, blank=True, default="")
    fire_note = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.container} - {self.fill_level}% - {self.status}"