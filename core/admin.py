from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import ProductPrompt, VideoGeneration, IPUsage
from django.conf import settings 
from django.contrib import messages 
from .tasks import send_video_ready_email_task 

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
    actions = ['send_completion_email'] 

    @admin.action(description='Send completion email for selected videos')
    def send_completion_email(self, request, queryset):
        """Triggers the completion email task for selected completed videos."""
        triggered_count = 0
        skipped_count = 0
        error_count = 0

        for video_gen in queryset:
            if video_gen.status == 'completed' and video_gen.email and video_gen.output_video_url:
                try:
                    send_video_ready_email_task.delay(video_gen.id)
                    triggered_count += 1
                except Exception as e:
                    messages.error(request, f"Failed to trigger email for ID {video_gen.id} ({video_gen.product_title}): {e}")
                    error_count += 1
            else:
                reason = []
                if video_gen.status != 'completed': reason.append(f"status is '{video_gen.status}'")
                if not video_gen.email: reason.append("no email address")
                if not video_gen.output_video_url: reason.append("no output video URL")
                messages.warning(request, f"Skipped email for ID {video_gen.id} ({video_gen.product_title}): {', '.join(reason)}.")
                skipped_count += 1

        if triggered_count:
            messages.success(request, f"Successfully triggered completion emails for {triggered_count} video generations.")
        if skipped_count:
             messages.info(request, f"Skipped {skipped_count} video generations (not completed, missing email, or missing URL). Check warnings.")
        if error_count:
             messages.error(request, f"Failed to trigger email task for {error_count} video generations. Check errors.")


@admin.register(IPUsage)
class IPUsageAdmin(admin.ModelAdmin):
    """Admin interface for IPUsage model."""
    list_display = ('ip_address', 'free_generations_used', 'last_used_at')
    search_fields = ('ip_address',)
    list_filter = ('last_used_at',)
    readonly_fields = ('last_used_at',)
    list_editable = ('free_generations_used',)
    ordering = ('-last_used_at',)


# Register models with custom admin interfaces
admin.site.register(ProductPrompt, ProductPromptAdmin)
admin.site.register(VideoGeneration, VideoGenerationAdmin)
