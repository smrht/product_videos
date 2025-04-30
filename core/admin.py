from django.contrib import admin
from .models import ProductPrompt, VideoGeneration

class ProductPromptAdmin(admin.ModelAdmin):
    """Admin interface for ProductPrompt model."""
    list_display = ('product_title', 'email', 'created_at', 'model_used', 'is_approved')
    list_filter = ('is_approved', 'model_used', 'created_at')
    search_fields = ('product_title', 'product_description', 'email', 'prompt_text')
    readonly_fields = ('id', 'created_at', 'task_id')
    fieldsets = (
        ('Product Information', {
            'fields': ('product_title', 'product_description', 'category')
        }),
        ('User Information', {
            'fields': ('email',)
        }),
        ('Prompt Data', {
            'fields': ('prompt_text', 'model_used', 'is_approved')
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'task_id'),
            'classes': ('collapse',)
        }),
    )


class VideoGenerationAdmin(admin.ModelAdmin):
    """Admin interface for VideoGeneration model."""
    list_display = ('product_title', 'email', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('product_title', 'email', 'product_description')
    readonly_fields = ('id', 'created_at', 'updated_at', 'task_id')
    fieldsets = (
        ('Product Information', {
            'fields': ('product_title', 'product_description')
        }),
        ('User Information', {
            'fields': ('email',)
        }),
        ('Files', {
            'fields': ('input_image_url', 'output_video_url')
        }),
        ('Status', {
            'fields': ('status', 'error_message')
        }),
        ('Relationships', {
            'fields': ('prompt',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at', 'task_id'),
            'classes': ('collapse',)
        }),
    )


# Register models with custom admin interfaces
admin.site.register(ProductPrompt, ProductPromptAdmin)
admin.site.register(VideoGeneration, VideoGenerationAdmin)
