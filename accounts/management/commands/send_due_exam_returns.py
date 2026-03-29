from datetime import datetime, time as dt_time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import Exam, Clinic, Veterinarian
from accounts.notifications import send_provider_return_email
from accounts.whatsapp_client import send_provider_return_whatsapp
from accounts.views import (
    ensure_pending_user_for_provider,
    build_activation_link,
    is_whatsapp_phone,
)


class Command(BaseCommand):
    help = "Envia alertas de retorno previsto vencidos para clínicas/veterinários."

    def _main_provider_from_exam(self, exam):
        name = (exam.clinic_or_vet or "").strip()
        if not name:
            return None

        clinic = Clinic.objects.filter(name__iexact=name).first()
        if clinic:
            return {
                "key": f"CLINIC:{clinic.id}",
                "obj": clinic,
                "label": clinic.name,
                "email": (clinic.email or "").strip(),
                "phone": (clinic.phone or "").strip(),
                "user": clinic.user,
            }

        vet = Veterinarian.objects.filter(name__iexact=name).first()
        if vet:
            return {
                "key": f"VET:{vet.id}",
                "obj": vet,
                "label": vet.name,
                "email": (vet.email or "").strip(),
                "phone": (vet.phone or "").strip(),
                "user": vet.user,
            }

        return None

    def _provider_from_token(self, token):
        token = (token or "").strip()
        if not token:
            return None

        try:
            kind, raw_id = token.split(":", 1)
            obj_id = int(raw_id)
        except Exception:
            return None

        if kind == "CLINIC":
            clinic = Clinic.objects.filter(id=obj_id).first()
            if clinic:
                return {
                    "key": f"CLINIC:{clinic.id}",
                    "obj": clinic,
                    "label": clinic.name,
                    "email": (clinic.email or "").strip(),
                    "phone": (clinic.phone or "").strip(),
                    "user": clinic.user,
                }

        if kind == "VET":
            vet = Veterinarian.objects.filter(id=obj_id).first()
            if vet:
                return {
                    "key": f"VET:{vet.id}",
                    "obj": vet,
                    "label": vet.name,
                    "email": (vet.email or "").strip(),
                    "phone": (vet.phone or "").strip(),
                    "user": vet.user,
                }

        return None

    def _collect_provider_targets(self, exam):
        seen = set()
        providers = []

        main_provider = self._main_provider_from_exam(exam)
        if main_provider and main_provider["key"] not in seen:
            seen.add(main_provider["key"])
            providers.append(main_provider)

        for token in (exam.additional_clinic_or_vet or []):
            provider = self._provider_from_token(token)
            if provider and provider["key"] not in seen:
                seen.add(provider["key"])
                providers.append(provider)

        return providers

    def handle(self, *args, **options):
        now = timezone.localtime()
        host = (getattr(settings, "CANONICAL_HOST", "") or "localhost").strip()

        request = RequestFactory().get("/", secure=True, HTTP_HOST=host)

        qs = Exam.objects.filter(
            retorno_previsto__isnull=False,
            retorno_previsto__lte=now.date(),
        ).order_by("retorno_previsto", "retorno_horario", "id")

        checked = 0
        processed = 0
        sent_count = 0

        for exam in qs.iterator():
            checked += 1

            return_time = exam.retorno_horario or dt_time(12, 0)
            target_naive = datetime.combine(exam.retorno_previsto, return_time)
            target_dt = timezone.make_aware(target_naive, timezone.get_current_timezone())

            if target_dt > now:
                continue

            if exam.retorno_alert_processed_for:
                processed_for_local = timezone.localtime(exam.retorno_alert_processed_for).replace(second=0, microsecond=0)
                target_compare = target_dt.replace(second=0, microsecond=0)

                if processed_for_local == target_compare:
                    continue

            provider_sent_any = False
            providers = self._collect_provider_targets(exam)

            for provider in providers:
                activation_link = None
                user = provider["user"]

                if user is None:
                    u, created_now, needs_activation = ensure_pending_user_for_provider(
                        name=provider["label"],
                        email=provider["email"],
                        phone=provider["phone"],
                        role="BASIC",
                    )
                    if u:
                        provider["obj"].user = u
                        provider["obj"].save(update_fields=["user"])
                        user = u
                        provider["user"] = u

                        if needs_activation:
                            activation_link = build_activation_link(request, u)

                elif not user.has_usable_password():
                    activation_link = build_activation_link(request, user)

                if provider["email"]:
                    try:
                        ok = send_provider_return_email(
                            request,
                            exam=exam,
                            to_email=provider["email"],
                            recipient_label=provider["label"],
                            activation_link=activation_link,
                        )
                        if ok:
                            provider_sent_any = True
                            sent_count += 1
                    except Exception as e:
                        self.stderr.write(
                            f"[Exam {exam.id}] Falha ao enviar e-mail de retorno para {provider['label']}: {e}"
                        )

                if provider["phone"] and is_whatsapp_phone(provider["phone"]):
                    try:
                        ok = send_provider_return_whatsapp(
                            request,
                            exam=exam,
                            to_phone=provider["phone"],
                            recipient_label=provider["label"],
                            activation_link=activation_link,
                        )
                        if ok:
                            provider_sent_any = True
                            sent_count += 1
                    except Exception as e:
                        self.stderr.write(
                            f"[Exam {exam.id}] Falha ao enviar WhatsApp de retorno para {provider['label']}: {e}"
                        )

            update_fields = [
                "retorno_alert_processed_at",
                "retorno_alert_processed_for",
            ]
            exam.retorno_alert_processed_at = now
            exam.retorno_alert_processed_for = target_dt

            if provider_sent_any and not exam.alerta_provider:
                exam.alerta_provider = True
                update_fields.append("alerta_provider")

            exam.save(update_fields=update_fields)
            processed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Retornos verificados: {checked}. "
                f"Retornos processados: {processed}. "
                f"Envios realizados: {sent_count}."
            )
        )
