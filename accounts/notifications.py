from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

def send_exam_email(request, *, exam, to_email: str, recipient_label: str, activation_link: str | None):
    """
    Envia e-mail informando que um exame foi cadastrado.
    Se activation_link vier preenchido, inclui o link para criar senha (1º contato).
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return False

    login_link = request.build_absolute_uri(reverse("login"))
    exam_link = request.build_absolute_uri(reverse("exam_view", args=[exam.pk]))

    subject = f"LumaVet — Exame cadastrado ({exam.exam_type})"

    # Texto (fallback)
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

    # HTML
    html = f"""
    <p>Olá, <b>{recipient_label}</b>!</p>
    <p>Um exame foi cadastrado no sistema.</p>
    <ul>
      <li><b>Clínica/Veterinário:</b> {exam.clinic_or_vet}</li>
      <li><b>Tutor:</b> {exam.tutor_name}</li>
      <li><b>Pet:</b> {exam.pet_name}</li>
      <li><b>Raça:</b> {exam.breed}</li>
      <li><b>Exame:</b> {exam.exam_type}</li>
      <li><b>Data:</b> {exam.date_realizacao.strftime('%d/%m/%Y')}</li>
    </ul>
    """

    if activation_link:
        html += f"""
        <p><b>Primeiro acesso:</b> crie sua senha aqui:</p>
        <p><a href="{activation_link}">{activation_link}</a></p>
        """

    html += f"""
    <p><a href="{login_link}">Fazer login</a></p>
    <p><a href="{exam_link}">Abrir exame (após login)</a></p>
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
