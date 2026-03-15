import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse


def normalize_br_phone(phone: str) -> str:
    """
    Converte números como:
    (21) 99036-4565 -> 5521990364565
    """
    digits = re.sub(r"\D", "", phone or "")

    if not digits:
        return ""

    if len(digits) == 11:
        return "55" + digits

    if len(digits) == 13 and digits.startswith("55"):
        return digits

    return ""


def _first_name_only(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else "cliente"


def _url_suffix_from_absolute_url(url: str) -> str:
    """
    Converte:
    https://lumavet.pet/ativar/abc/def/  -> /ativar/abc/def/
    https://lumavet.pet/login/           -> /login/
    """
    parsed = urlsplit(url)
    suffix = parsed.path or "/"

    if parsed.query:
        suffix += f"?{parsed.query}"
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"

    return suffix


def _post_whatsapp_payload(payload: dict) -> dict:
    if not settings.WHATSAPP_ENABLED:
        return {}

    if not settings.WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID não configurado.")

    if not settings.WHATSAPP_TOKEN:
        raise RuntimeError("WHATSAPP_TOKEN não configurado.")

    url = (
        f"https://graph.facebook.com/"
        f"{settings.WHATSAPP_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

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
            return json.loads(response_body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Erro HTTP {e.code} no WhatsApp: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Falha de conexão com a API do WhatsApp: {e}")


def _send_template_message(
    *,
    to_phone: str,
    template_name: str,
    body_parameters: list[str] | None = None,
    button_url_suffix: str | None = None,
    button_index: str = "0",
) -> bool:
    if not settings.WHATSAPP_ENABLED:
        return False

    normalized_phone = normalize_br_phone(to_phone)
    if not normalized_phone:
        return False

    if not template_name:
        raise RuntimeError("Template do WhatsApp não configurado.")

    template_payload = {
        "name": template_name,
        "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
    }

    components = []

    if body_parameters:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": str(value)} for value in body_parameters],
            }
        )

    if button_url_suffix is not None:
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": str(button_index),
                "parameters": [
                    {
                        "type": "text",
                        "text": button_url_suffix,
                    }
                ],
            }
        )

    if components:
        template_payload["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "to": normalized_phone,
        "type": "template",
        "template": template_payload,
    }

    data = _post_whatsapp_payload(payload)
    return "messages" in data


def send_tutor_exam_whatsapp(request, *, exam, to_phone: str, activation_link: str | None = None) -> bool:
    """
    Casos 1 e 2:
    - Tutor em primeiro acesso
    - Tutor com acesso já existente
    """
    tutor_first_name = _first_name_only(exam.tutor_name)
    exam_date = exam.date_realizacao.strftime("%d/%m/%Y")
    login_link = request.build_absolute_uri(reverse("login"))

    is_first_access = bool(activation_link)
    target_link = activation_link or login_link
    target_suffix = _url_suffix_from_absolute_url(target_link)

    template_name = (
        settings.WHATSAPP_TEMPLATE_TUTOR_EXAM_FIRST_ACCESS
        if is_first_access
        else settings.WHATSAPP_TEMPLATE_TUTOR_EXAM_EXISTING_ACCESS
    )

    if is_first_access and not settings.WHATSAPP_TEMPLATE_TUTOR_EXAM_FIRST_ACCESS:
        raise RuntimeError("WHATSAPP_TEMPLATE_TUTOR_EXAM_FIRST_ACCESS não configurado.")

    if not is_first_access and not settings.WHATSAPP_TEMPLATE_TUTOR_EXAM_EXISTING_ACCESS:
        raise RuntimeError("WHATSAPP_TEMPLATE_TUTOR_EXAM_EXISTING_ACCESS não configurado.")

    return _send_template_message(
        to_phone=to_phone,
        template_name=template_name,
        body_parameters=[
            tutor_first_name,
            exam.pet_name,
            exam.exam_type,
            exam_date,
        ],
        button_url_suffix=target_suffix,
        button_index="0",
    )


def send_exam_whatsapp(request, *, exam, to_phone: str, recipient_label: str, activation_link: str | None = None) -> bool:
    """
    Envio genérico atual (mantido para clínica/veterinário por enquanto).
    """
    if not settings.WHATSAPP_TEMPLATE_NAME:
        raise RuntimeError("WHATSAPP_TEMPLATE_NAME não configurado.")

    login_link = request.build_absolute_uri(reverse("login"))
    target_link = activation_link or login_link

    if settings.WHATSAPP_TEMPLATE_NAME == "hello_world":
        body_parameters = None
    else:
        body_parameters = [
            recipient_label,
            exam.pet_name,
            exam.exam_type,
            target_link,
        ]

    return _send_template_message(
        to_phone=to_phone,
        template_name=settings.WHATSAPP_TEMPLATE_NAME,
        body_parameters=body_parameters,
    )
