"""Runtime readiness and corrupt mechanics have separate smoke outcomes."""
import sys

from app import config, pdf_ocr


def test_actual_worker_process_denies_python_socket_network(monkeypatch):
    monkeypatch.setattr(config, 'PDF_OCR_PADDLE_PYTHON', sys.executable)
    assert pdf_ocr.worker_network_policy() == {
        'connect_denied': True, 'connect_ex_denied': True, 'create_connection_denied': True}


def test_ld6_corruption_is_a_passing_safety_case_and_never_accepted():
    from scripts.experiments.smoke_pdf_ocr_cpu import evaluate_smoke

    result = evaluate_smoke(
        {'model_state': 'ready', 'device': 'cpu', 'enabled': True},
        {'status': 'candidate', 'candidate': 'ARCHIVE ROOM\nNORTH DOOR\nSOUTH HALL'},
        {'status': 'candidate', 'candidate': 'STR 60 DEX 55 HP 12\nDamage ld6+2'},
        {'connect_denied': True, 'connect_ex_denied': True, 'create_connection_denied': True})
    assert result['runtime_positive'] is True
    assert result['mechanics_corruption_rejection'] is True
    assert result['observed_dice_candidate_accepted'] is False
    assert result['passed'] is True
