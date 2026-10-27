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
