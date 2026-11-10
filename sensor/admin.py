from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import (
    Container,
    District,
    RequestAnalysis,
    RequestEvent,
    ResidentRequest,
    SensorReading,
    StaffProfile,
)


class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    can_delete = False
    verbose_name_plural = "Аудан (бос қалса — бүкіл қала)"
    readonly_fields = ("latitude", "longitude", "located_at")


admin.site.unregister(get_user_model())


@admin.register(get_user_model())
class StaffUserAdmin(UserAdmin):
    inlines = (StaffProfileInline,)


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    exclude = ("boundary",)


class RequestEventInline(admin.TabularInline):
    model = RequestEvent
    extra = 0
    readonly_fields = ("created_at", "status", "actor", "note")
    can_delete = False


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "location", "district", "latitude", "longitude")
    list_filter = ("district",)
    search_fields = ("name", "location")


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("created_at", "container", "fill_level", "temperature", "gas_level", "fire_risk")
    list_filter = ("container", "fire_risk")
    date_hierarchy = "created_at"


@admin.register(ResidentRequest)
class ResidentRequestAdmin(admin.ModelAdmin):
    list_display = ("code", "category", "status", "district", "address", "assignee", "created_at")
    list_filter = ("status", "category", "district")
    search_fields = ("code", "address")
    readonly_fields = ("code", "container", "container_distance", "sensor_fill_level", "sensor_reading_at")
    inlines = (RequestEventInline,)


@admin.register(RequestAnalysis)
class RequestAnalysisAdmin(admin.ModelAdmin):
    list_display = ("request", "state", "waste_type", "hazard", "confidence", "needs_review", "created_at")
    list_filter = ("state", "hazard", "needs_review")
