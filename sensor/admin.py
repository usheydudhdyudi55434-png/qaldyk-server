from django.contrib import admin

from .models import Container, RequestEvent, ResidentRequest, SensorReading


class RequestEventInline(admin.TabularInline):
    model = RequestEvent
    extra = 0
    readonly_fields = ("created_at", "status", "actor", "note")
    can_delete = False


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "location", "latitude", "longitude")
    search_fields = ("name", "location")


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("created_at", "container", "fill_level", "temperature", "gas_level", "fire_risk")
    list_filter = ("container", "fire_risk")
    date_hierarchy = "created_at"


@admin.register(ResidentRequest)
class ResidentRequestAdmin(admin.ModelAdmin):
    list_display = ("code", "category", "status", "address", "assignee", "created_at")
    list_filter = ("status", "category")
    search_fields = ("code", "address")
    readonly_fields = ("code", "container", "container_distance", "sensor_fill_level", "sensor_reading_at")
    inlines = (RequestEventInline,)
