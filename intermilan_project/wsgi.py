import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "intermilan_project.settings.production")

# Run collectstatic before serving — ensures manifest is generated at startup.
# Idempotent; safe with multiple Gunicorn workers (Django serializes internally).
from django.core.management import execute_from_command_line
execute_from_command_line(["manage.py", "collectstatic", "--noinput", "--settings=intermilan_project.settings.production"])

application = get_wsgi_application()
