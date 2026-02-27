import os
import smtplib
import ssl
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Testa login SMTP com as env vars atuais"

    def handle(self, *args, **options):
        host = os.environ.get("EMAIL_HOST")
        port = int(os.environ.get("EMAIL_PORT", "587"))
        user = os.environ.get("EMAIL_HOST_USER")
        password = os.environ.get("EMAIL_HOST_PASSWORD")
        
        if not host or not user or not password:
            raise SystemExit("Faltam EMAIL_HOST / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD nas variáveis de ambiente.")

        self.stdout.write(f"HOST={host} PORT={port} USER={user} PASS_LEN={len(password) if password else 0}")

        smtp = smtplib.SMTP(host=host, port=port, timeout=20)
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(user, password)
        self.stdout.write("LOGIN OK")
        smtp.quit()
