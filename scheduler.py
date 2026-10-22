"""
Agendamento de horarios: regras que param downloads e/ou uploads em certos
dias e horarios.

Cada regra e' um dicionario:

    {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00",
     "download": False, "upload": True}

- days: dias da semana em que a regra COMECA (0 = segunda ... 6 = domingo)
- start/end: "HH:MM". Se end < start a regra atravessa a meia-noite e termina
  no dia seguinte. Se end == start a regra vale o dia inteiro.
- download/upload: True = liberado, False = parado durante a regra.

Com mais de uma regra ativa ao mesmo tempo, vence a mais restritiva: basta uma
regra parar o download para ele ficar parado.

Este modulo so' calcula o efeito; quem aplica na sessao e' o TorrentEngine.
"""

from datetime import datetime

DAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]


def parse_hhmm(text: str) -> int:
    """Converte "HH:MM" em minutos desde a meia-noite. Levanta ValueError se invalido."""
    try:
        hh, mm = text.strip().split(":")
        h, m = int(hh), int(mm)
    except (AttributeError, ValueError):
        raise ValueError(f'Horario invalido: "{text}" (use HH:MM, ex.: 08:30)') from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f'Horario invalido: "{text}" (use HH:MM entre 00:00 e 23:59)')
    return h * 60 + m


def validate_rule(rule: dict) -> dict:
    """Confere e normaliza uma regra. Levanta ValueError com mensagem legivel."""
    days = sorted({int(d) for d in rule.get("days", [])})
    if not days:
        raise ValueError("Escolha pelo menos um dia da semana.")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("Dia da semana invalido.")
    start = rule.get("start", "")
    end = rule.get("end", "")
    parse_hhmm(start)
    parse_hhmm(end)
    download = bool(rule.get("download", True))
    upload = bool(rule.get("upload", True))
    if download and upload:
        raise ValueError("A regra nao para nada: marque download e/ou upload como parado.")
    return {
        "days": days,
        "start": f"{parse_hhmm(start) // 60:02d}:{parse_hhmm(start) % 60:02d}",
        "end": f"{parse_hhmm(end) // 60:02d}:{parse_hhmm(end) % 60:02d}",
        "download": download,
        "upload": upload,
    }


def rule_active(rule: dict, now: datetime) -> bool:
    start = parse_hhmm(rule["start"])
    end = parse_hhmm(rule["end"])
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    days = rule["days"]

    if start == end:
        return weekday in days
    if start < end:
        return weekday in days and start <= minute < end
    # atravessa a meia-noite: a parte da noite pertence ao dia listado e a parte
    # da madrugada, ao dia seguinte
    return (weekday in days and minute >= start) or ((weekday - 1) % 7 in days and minute < end)


def current_effect(rules: list[dict], now: datetime) -> tuple[bool, bool, list[dict]]:
    """Retorna (download_liberado, upload_liberado, regras_ativas)."""
    active = []
    for rule in rules:
        try:
            if rule_active(rule, now):
                active.append(rule)
        except (KeyError, ValueError):
            continue  # regra corrompida no settings.json: ignora em vez de travar
    download = all(r.get("download", True) for r in active)
    upload = all(r.get("upload", True) for r in active)
    return download, upload, active


def describe_days(days: list[int]) -> str:
    days = sorted(days)
    if days == list(range(7)):
        return "Todos os dias"
    if days == list(range(5)):
        return "Seg a Sex"
    if days == [5, 6]:
        return "Sab e Dom"
    return ", ".join(DAY_NAMES[d] for d in days)


def describe_effect(download: bool, upload: bool) -> str | None:
    if download and upload:
        return None
    if not download and not upload:
        return "downloads e uploads parados"
    if not download:
        return "downloads parados (upload continua)"
    return "uploads parados (download continua)"
