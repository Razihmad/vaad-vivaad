from django.contrib import admin
from unfold.admin import ModelAdmin

# Register your models here.
from users.models import (
    UserDevice,
    UserProfile,
    ApplicationConfig,
    TopicComment,
    TopicVote,
)


@admin.register(UserDevice)
class UserDeviceAdmin(ModelAdmin):
    list_display = ("user", "device_id", "device_type", "is_active")
    list_filter = ("device_type", "is_active")
    search_fields = ("user__username", "device_id")
    readonly_fields = ("user",)


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ("elo_rating", "total_debates", "wins", "losses")
    list_filter = ("elo_rating", "total_debates", "wins", "losses")
    search_fields = ("user__username",)
    readonly_fields = ("user",)


@admin.register(ApplicationConfig)
class ApplicationConfigAdmin(ModelAdmin):
    list_display = ("name", "properties", "is_active")
    search_fields = ("name",)
    list_filter = ("is_active",)


@admin.register(TopicComment)
class TopicCommentAdmin(ModelAdmin):
    list_display = ("topic", "user", "comment", "side")
    search_fields = ("topic", "user", "side")
    list_filter = ("side", "topic")


@admin.register(TopicVote)
class TopicVoteAdmin(ModelAdmin):
    list_display = ("topic", "user", "side")
    search_fields = ("topic", "user", "side")
    list_filter = ("side", "topic")
