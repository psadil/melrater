"""ASGI config for melrater (granian's entrypoint — see the serve task).

Importable only with src/ on the import path; pixi's activation.env
provides that (and DJANGO_SETTINGS_MODULE) for every task.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
