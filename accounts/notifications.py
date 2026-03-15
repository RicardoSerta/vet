from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse


def _first_name_only(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else "cliente"


def send_exam_email(request, *, exam, to_email: str, recipient_label: str, activation_link: str | None):
    """
    Envio genérico atual (mantido para clínica/veterinário por enquanto).
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False

    login_link = request.build_absolute_uri(reverse("login"))
    exam_link = request.build_absolute_uri(reverse("exam_view", args=[exam.pk]))

    subject = f"LumaVet — Exame cadastrado ({exam.exam_type})"

    lines = [
        f"Olá, {recipient_label}!",
        "",
        "Um exame foi cadastrado no sistema.",
        "",
        f"Clínica/Veterinário: {exam.clinic_or_vet}",
        f"Tutor: {exam.tutor_name}",
        f"Pet: {exam.pet_name}",
        f"Raça: {exam.breed}",
        f"Exame: {exam.exam_type}",
        f"Data de realização: {exam.date_realizacao.strftime('%d/%m/%Y')}",
        "",
    ]

    if activation_link:
        lines += [
            "Este é seu primeiro acesso.",
            f"Crie sua senha por aqui: {activation_link}",
            "",
        ]

    lines += [
        f"Login: {login_link}",
        f"Abrir exame (após login): {exam_link}",
        "",
        "— LumaVet",
    ]

    text_body = "\n".join(lines)

    html = f"""
    <p>Olá, <b>{escape(recipient_label)}</b>!</p>
    <p>Um exame foi cadastrado no sistema.</p>
    <ul>
      <li><b>Clínica/Veterinário:</b> {escape(exam.clinic_or_vet)}</li>
      <li><b>Tutor:</b> {escape(exam.tutor_name)}</li>
      <li><b>Pet:</b> {escape(exam.pet_name)}</li>
      <li><b>Raça:</b> {escape(exam.breed)}</li>
      <li><b>Exame:</b> {escape(exam.exam_type)}</li>
      <li><b>Data:</b> {escape(exam.date_realizacao.strftime('%d/%m/%Y'))}</li>
    </ul>
    """

    if activation_link:
        html += f"""
        <p><b>Primeiro acesso:</b> crie sua senha aqui:</p>
        <p><a href="{escape(activation_link, quote=True)}">{escape(activation_link)}</a></p>
        """

    html += f"""
    <p><a href="{escape(login_link, quote=True)}">Fazer login</a></p>
    <p><a href="{escape(exam_link, quote=True)}">Abrir exame (após login)</a></p>
    <p>— LumaVet</p>
    """

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html, "text/html")
    msg.send()
    return True


def send_tutor_exam_email(request, *, exam, to_email: str, activation_link: str | None):
    """
    Casos 1 e 2:
    - Tutor em primeiro acesso
    - Tutor com acesso já existente
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False

    tutor_first_name = _first_name_only(exam.tutor_name)
    exam_date = exam.date_realizacao.strftime("%d/%m/%Y")
    login_link = request.build_absolute_uri(reverse("login"))

    is_first_access = bool(activation_link)
    target_link = activation_link or login_link

    subject = f"LumaVet — Exame de {exam.pet_name} cadastrado"

    lines = [
        f"Olá, {tutor_first_name}!",
        "",
        "Um exame foi cadastrado no LumaVet.",
        "",
        "Dados do exame:",
        f"Pet: {exam.pet_name}",
        f"Exame: {exam.exam_type}",
        f"Realização: {exam_date}",
        "",
    ]

    if is_first_access:
        lines += [
            "Este é o seu primeiro acesso ao portal.",
            "",
            "Para criar sua senha e visualizar o exame, clique no link abaixo:",
        ]
    else:
        lines += [
            "Para acessar o portal e visualizar o exame, clique no link abaixo:",
        ]

    lines += [
        target_link,
        "",
        "Atenciosamente,",
        "Equipe LumaVet",
    ]

    text_body = "\n".join(lines)

    html_parts = [
        f"<p>Olá, {escape(tutor_first_name)}!</p>",
        "<p>Um exame foi cadastrado no LumaVet.</p>",
        (
            "<p><strong>Dados do exame:</strong><br>"
            f"Pet: {escape(exam.pet_name)}<br>"
            f"Exame: {escape(exam.exam_type)}<br>"
            f"Realização: {escape(exam_date)}"
            "</p>"
        ),
    ]

    if is_first_access:
        html_parts.append("<p>Este é o seu primeiro acesso ao portal.</p>")
        html_parts.append("<p>Para criar sua senha e visualizar o exame, clique no link abaixo:</p>")
    else:
        html_parts.append("<p>Para acessar o portal e visualizar o exame, clique no link abaixo:</p>")

    html_parts.append(
        f'<p><a href="{escape(target_link, quote=True)}">{escape(target_link)}</a></p>'
    )
    html_parts.append("<p>Atenciosamente,<br>Equipe LumaVet</p>")

    html_body = "\n".join(html_parts)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()
    return True
