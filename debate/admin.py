from django.contrib import admin
from unfold.admin import ModelAdmin
from debate.models import Topic, Debate, Round, Message, MatchQueue, Category


@admin.register(Topic)
class TopicAdmin(ModelAdmin):
    list_display = ("title", "description", "category", "is_active", "is_trending")
    list_filter = ("is_active", "is_trending", "category")
    search_fields = ("title", "description")


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("name", "description", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "description")


@admin.register(Debate)
class DebateAdmin(ModelAdmin):
    list_display = (
        "topic",
        "user_pro",
        "user_con",
        "status",
        "started_at",
        "completed_at",
    )
    list_filter = ("status",)
    search_fields = ("topic__title", "user_pro__username", "user_con__username")


@admin.register(Round)
class RoundAdmin(ModelAdmin):
    list_display = ("debate", "round_type", "order", "started_at", "ended_at")
    list_filter = ("round_type",)
    search_fields = (
        "debate__topic__title",
        "debate__user_pro__username",
        "debate__user_con__username",
    )


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = ("debate", "round", "user", "content", "created_at")
    list_filter = ("debate__topic__title", "user__username")
    search_fields = ("debate__topic__title", "user__username", "content")


@admin.register(MatchQueue)
class MatchQueueAdmin(ModelAdmin):
    list_display = ("topic", "user", "status", "joined_at", "matched_at")
    list_filter = ("status",)
    search_fields = ("topic__title", "user__username")
