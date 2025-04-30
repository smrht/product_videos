from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        """
        Import and register celery tasks when Django starts.
        This ensures that the Celery worker can find all tasks.
        """
        import core.tasks  # noqa
