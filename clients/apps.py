from django.apps import AppConfig

class ClientsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "clients"

    def ready(self):
        import clients.signals
        import clients.middleware  # registers the login receiver that stamps the session's day


