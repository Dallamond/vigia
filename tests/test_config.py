"""Tests de carga/validacion de config.yaml (sin tocar la config real)."""
import textwrap

from vigia.config import DEFAULTS, cargar_config


def test_sin_fichero_devuelve_defaults(tmp_path):
    cfg = cargar_config(tmp_path / "no-existe.yaml")
    assert cfg == DEFAULTS


def test_yaml_invalido_no_rompe(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("esto: [no: es: yaml", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == DEFAULTS["permanencia_seg"]


def test_yaml_que_no_es_diccionario_no_rompe(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("- uno\n- dos\n", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["timeout_objeto_seg"] == DEFAULTS["timeout_objeto_seg"]


def test_tipos_invalidos_se_corrigen(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        permanencia_seg: "mucho"
        timeout_objeto_seg: "10"
        timeout_persona_seg: []
        zonas: "puerta"
        franjas: "01:00-05:00"
        vigilar_desaparicion: "laptop"
        guardar_clips: "si"
        notificar: 3
    """), encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == DEFAULTS["permanencia_seg"]
    assert isinstance(cfg["timeout_objeto_seg"], float)
    assert cfg["timeout_persona_seg"] == DEFAULTS["timeout_persona_seg"]
    assert cfg["zonas"] == {}
    assert cfg["franjas"] == []
    assert cfg["vigilar_desaparicion"] == []
    assert cfg["guardar_clips"] == DEFAULTS["guardar_clips"]
    assert cfg["notificar"] == DEFAULTS["notificar"]


def test_zona_con_forma_incorrecta_se_descarta(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        zonas:
          puerta: [0, 0, 50]
          salon: [0, 0, 100, 100]
    """), encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["zonas"] == {"salon": (0, 0, 100, 100)}


def test_valores_validos_se_respetan(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        permanencia_seg: 5
        guardar_clips: false
        zonas:
          puerta: [1, 2, 3, 4]
    """), encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == 5
    assert cfg["guardar_clips"] is False
    assert cfg["zonas"] == {"puerta": (1, 2, 3, 4)}


def test_defaults_incluyen_captura_y_retencion():
    cfg = cargar_config("no-existe-en-absoluto.yaml")
    assert cfg["ancho"] == 640 and cfg["alto"] == 480
    assert cfg["miniaturas"] is True
    assert cfg["retencion_dias"] > 0
    assert cfg["retencion_clips_mb"] > 0


def test_resolucion_y_retencion_se_acotan(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        ancho: 99999
        alto: 1
        retencion_dias: -5
        mantener_cada_min: 0
        miniaturas: "si"
    """), encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["ancho"] == 3840
    assert cfg["alto"] == 120
    assert cfg["retencion_dias"] == 0
    assert cfg["mantener_cada_min"] == 1
    assert cfg["miniaturas"] is True  # "si" no es booleano yaml -> default


def test_resolucion_valida_se_respeta(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ancho: 1280\nalto: 720\n", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["ancho"] == 1280 and cfg["alto"] == 720
