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
    assert set(cfg["zonas"]) == {"salon"}
    assert cfg["zonas"]["salon"].caja == (0, 0, 100, 100)


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
    assert cfg["zonas"]["puerta"].caja == (1, 2, 3, 4)


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


# -- regresiones: tiempos no positivos y claves desconocidas -------------

def test_tiempos_no_positivos_caen_al_default(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        permanencia_seg: -5
        timeout_objeto_seg: 0
        timeout_persona_seg: -0.1
    """), encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == DEFAULTS["permanencia_seg"]
    assert cfg["timeout_objeto_seg"] == DEFAULTS["timeout_objeto_seg"]
    assert cfg["timeout_persona_seg"] == DEFAULTS["timeout_persona_seg"]


def test_tiempos_demasiado_grandes_se_acotan(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("permanencia_seg: 999999\ntimeout_objeto_seg: 90000\n",
                 encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == 86400
    assert cfg["timeout_objeto_seg"] == 86400


def test_clave_desconocida_avisa_pero_no_rompe(tmp_path, caplog):
    p = tmp_path / "c.yaml"
    p.write_text("permanencia_seg: 12\npermanencia_segs: 99\n",
                 encoding="utf-8")
    with caplog.at_level("WARNING", logger="vigia.config"):
        cfg = cargar_config(p)
    assert cfg["permanencia_seg"] == 12
    assert "permanencia_segs" in caplog.text


def test_caras_es_booleano_y_por_defecto_false(tmp_path):
    cfg = cargar_config(tmp_path / "no.yaml")
    assert cfg["caras"] is False
    p = tmp_path / "c.yaml"
    p.write_text("caras: true\n", encoding="utf-8")
    assert cargar_config(p)["caras"] is True
    p.write_text("caras: \"si\"\n", encoding="utf-8")
    assert cargar_config(p)["caras"] is False  # no booleano: default


def test_host_y_puerto_en_config(tmp_path):
    cfg = cargar_config(tmp_path / "no.yaml")
    assert cfg["host"] == "127.0.0.1" and cfg["puerto"] == 8000
    p = tmp_path / "c.yaml"
    p.write_text("host: 0.0.0.0\npuerto: 8010\n", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["host"] == "0.0.0.0" and cfg["puerto"] == 8010
    p.write_text("host: ''\npuerto: 99999\n", encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["host"] == "127.0.0.1" and cfg["puerto"] == 65535


def test_bloque_4_y_telegram_tienen_defaults_seguros(tmp_path):
    cfg = cargar_config(tmp_path / "no.yaml")
    assert cfg["telegram"] is False and cfg["armado_auto"] is False
    assert cfg["telegram_prioridades"]["permanencia"] == "aviso"
    assert cfg["telegram_prioridades"]["persona_entra"] == "info"
    assert cfg["permanencia_conocidos_seg"] == 0
    assert cfg["inmovil_seg"] == 0  # desactivado por defecto
    assert cfg["merodeo_entradas"] >= 2
    p = tmp_path / "c.yaml"
    p.write_text(
        "telegram: true\n"
        "telegram_prioridades:\n"
        "  permanencia: critico\n"
        "  inventada: mucho\n"
        "inmovil_seg: -5\n"
        "merodeo_entradas: 0\n",
        encoding="utf-8")
    cfg = cargar_config(p)
    assert cfg["telegram"] is True
    assert cfg["telegram_prioridades"]["permanencia"] == "critico"
    assert "inventada" not in cfg["telegram_prioridades"]
    assert cfg["inmovil_seg"] == 0
    assert cfg["merodeo_entradas"] == 1
