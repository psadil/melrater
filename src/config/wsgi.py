"""WSGI config for melrater.

Importable only with src/ on the import path; pixi's activation.env
provides that (and DJANGO_SETTINGS_MODULE) for every task.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
