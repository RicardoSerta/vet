#!/usr/bin/env bash
set -o errexit  # se der erro, o script para

pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate

