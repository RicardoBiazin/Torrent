"""Testes do agendamento: regras puras (scheduler.py) e o efeito na engine."""

from datetime import datetime

import libtorrent as lt
import pytest

import scheduler
from test_engine import OfflineEngine, _espera, ambiente  # noqa: F401 - fixture

# 21/10/2026 e' uma quarta-feira (weekday 2)
QUA = datetime(2026, 10, 21)


def em(dia: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return dia.replace(hour=h, minute=m)


def test_parse_hhmm():
    assert scheduler.parse_hhmm("00:00") == 0
    assert scheduler.parse_hhmm("8:05") == 485
    assert scheduler.parse_hhmm("23:59") == 1439
    for ruim in ("24:00", "12:60", "abc", "", "12"):
        with pytest.raises(ValueError):
            scheduler.parse_hhmm(ruim)


def test_validate_rule():
    r = scheduler.validate_rule({"days": [4, 0, 0], "start": "8:0", "end": "18:00",
                                 "download": False, "upload": True})
    assert r == {"days": [0, 4], "start": "08:00", "end": "18:00", "download": False, "upload": True}
    with pytest.raises(ValueError):
        scheduler.validate_rule({"days": [], "start": "08:00", "end": "09:00", "download": False})
    with pytest.raises(ValueError):  # regra que nao para nada
        scheduler.validate_rule({"days": [0], "start": "08:00", "end": "09:00",
                                 "download": True, "upload": True})


def test_regra_no_mesmo_dia():
    r = {"days": [2], "start": "08:00", "end": "18:00", "download": False, "upload": True}
    assert not scheduler.rule_active(r, em(QUA, "07:59"))
    assert scheduler.rule_active(r, em(QUA, "08:00"))
    assert scheduler.rule_active(r, em(QUA, "17:59"))
    assert not scheduler.rule_active(r, em(QUA, "18:00"))  # fim exclusivo
    assert not scheduler.rule_active(r, em(QUA.replace(day=22), "12:00"))  # quinta


def test_regra_que_atravessa_meia_noite():
    r = {"days": [2], "start": "23:00", "end": "06:00", "download": False, "upload": False}
    assert scheduler.rule_active(r, em(QUA, "23:30"))
    assert scheduler.rule_active(r, em(QUA.replace(day=22), "05:59"))  # madrugada de quinta
    assert not scheduler.rule_active(r, em(QUA.replace(day=22), "06:00"))
    assert not scheduler.rule_active(r, em(QUA, "05:00"))  # madrugada de quarta e' da terca


def test_regra_dia_inteiro_e_mais_restritiva_vence():
    dia_todo = {"days": [2], "start": "00:00", "end": "00:00", "download": True, "upload": False}
    tarde = {"days": [2], "start": "12:00", "end": "14:00", "download": False, "upload": True}
    down, up, ativas = scheduler.current_effect([dia_todo, tarde], em(QUA, "13:00"))
    assert (down, up, len(ativas)) == (False, False, 2)
    down, up, _ = scheduler.current_effect([dia_todo, tarde], em(QUA, "09:00"))
    assert (down, up) == (True, False)
    # regra corrompida e' ignorada
    assert scheduler.current_effect([{"days": [2]}], em(QUA, "09:00"))[:2] == (True, True)


def test_descricoes():
    assert scheduler.describe_days(list(range(5))) == "Seg a Sex"
    assert scheduler.describe_days([5, 6]) == "Sab e Dom"
    assert scheduler.describe_days([0, 2]) == "Seg, Qua"
    assert scheduler.describe_effect(True, True) is None
    assert "parados" in scheduler.describe_effect(False, False)


def _engine_com_regra(ambiente, regra):  # noqa: F811
    settings = dict(ambiente["settings"], schedule_enabled=True, schedule=[regra])
    eng = OfflineEngine(settings)
    h = eng.add_torrent(ambiente["torrent"])
    assert _espera(lambda: eng.list_statuses()[0]["is_seeding"])
    return eng, h


def test_engine_para_tudo_e_volta(ambiente):  # noqa: F811
    eng, h = _engine_com_regra(ambiente, {"days": [2], "start": "08:00", "end": "18:00",
                                          "download": False, "upload": False})
    assert eng.apply_schedule(em(QUA, "09:00")) == "downloads e uploads parados"
    assert eng.session.is_paused()
    # o "pausado" do torrent (do usuario) nao foi tocado
    assert eng.jobs[h]["paused"] is False
    assert eng.apply_schedule(em(QUA, "19:00")) is None
    assert not eng.session.is_paused()


def test_engine_so_download_parado(ambiente):  # noqa: F811
    eng, h = _engine_com_regra(ambiente, {"days": [2], "start": "08:00", "end": "18:00",
                                          "download": False, "upload": True})
    eng.apply_schedule(em(QUA, "09:00"))
    assert eng.handles[h].flags() & lt.torrent_flags.upload_mode
    assert not eng.session.is_paused()

    # ao fechar dentro da regra, o upload_mode nao pode ir para a retomada
    eng.shutdown()
    assert not eng.handles[h].flags() & lt.torrent_flags.upload_mode


def test_engine_so_upload_parado(ambiente):  # noqa: F811
    eng, _h = _engine_com_regra(ambiente, {"days": [2], "start": "08:00", "end": "18:00",
                                           "download": True, "upload": False})
    eng.apply_schedule(em(QUA, "09:00"))
    assert eng.session.get_settings()["upload_rate_limit"] == 1
    eng.apply_schedule(em(QUA, "20:00"))
    assert eng.session.get_settings()["upload_rate_limit"] == 100 * 1024

    # salvar configuracoes no meio da regra nao pode "soltar" o upload
    eng.apply_schedule(em(QUA, "09:00"))
    eng.settings["max_up_kbs"] = 50
    eng.session.apply_settings(eng._rate_limits())
    assert eng.session.get_settings()["upload_rate_limit"] == 1


def test_agendamento_desligado_nao_faz_nada(ambiente):  # noqa: F811
    eng, h = _engine_com_regra(ambiente, {"days": [2], "start": "08:00", "end": "18:00",
                                          "download": False, "upload": False})
    eng.settings["schedule_enabled"] = False
    assert eng.apply_schedule(em(QUA, "09:00")) is None
    assert not eng.session.is_paused()
