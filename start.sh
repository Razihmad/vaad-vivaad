#!/bin/sh
set -e

python manage.py migrate --noinput

celery -A project worker -l info --without-mingle --without-gossip &

daphne -b 0.0.0.0 -p 8000 project.asgi:application
