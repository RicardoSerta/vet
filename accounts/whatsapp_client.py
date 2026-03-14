import json
import re
import urllib.request
import urllib.error

from django.conf import settings
from django.urls import reverse


def normalize_br_phone(phone: str) -> str:
    """
    Converte números como:
    (21) 99036-4565 -> 5521990364565

    Retorna string vazia se não conseguir normalizar.
    """
    digits = re.sub(r"\D", "", phone or "")

    if not digits:
        return ""

    # 11 dígitos brasileiros: DDD + 9 + número
    if len(digits) == 11:
        return "55" + digits

    # já veio com 55 + 11 dígitos
    if len(digits) == 13 and digits.startswith("55"):
        return digits

    return ""


def send_exam_whatsapp(request, *, exam, to_phone: str, recipient_label: str, activation_link: str | None = None) -> bool:
    """
    Envia template do WhatsApp para avisar que um exame foi cadastrado.
    Retorna True se a API aceitar o envio, senão lança exceção.
    """
    if not settings.WHATSAPP_ENABLED:
        return False

    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID não configurado.")

    if not settings.WHATSAPP_TOKEN:
        raise RuntimeError("WHATSAPP_TOKEN não configurado.")

    if not settings.WHATSAPP_TEMPLATE_NAME:
        raise RuntimeError("WHATSAPP_TEMPLATE_NAME não configurado.")

    normalized_phone = normalize_br_phone(to_phone)
    if not normalized_phone:
        return False

    login_link = request.build_absolute_uri(reverse("login"))
    target_link = activation_link or login_link

    url = (
        f"https://graph.facebook.com/"
        f"{settings.WHATSAPP_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

    template_payload = {
        "name": settings.WHATSAPP_TEMPLATE_NAME,
        "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
    }

    if settings.WHATSAPP_TEMPLATE_NAME != "hello_world":
        template_payload["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": recipient_label},
                    {"type": "text", "text": exam.pet_name},
                    {"type": "text", "text": exam.exam_type},
                    {"type": "text", "text": target_link},
                ],
            }
        ]

    payload = {
        "messaging_product": "whatsapp",
        "to": normalized_phone,
        "type": "template",
        "template": template_payload,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response_body = response.read().decode("utf-8")
            data = json.loads(response_body)
            return "messages" in data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Erro HTTP {e.code} no WhatsApp: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Falha de conexão com a API do WhatsApp: {e}")
