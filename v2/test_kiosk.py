"""El script del kiosco.

Se prueba porque ya rompio dos veces por lo mismo: el binario de Chromium se
llama distinto segun la version de Raspberry Pi OS. Es shell, asi que la
prueba lo ejecuta de verdad con un PATH de mentira en vez de leerlo.
"""

import os
import shutil
import subprocess
import textwrap

import pytest

KIOSK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiosk.sh")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="requiere bash")


def correr(path_falso, extra_env=None):
    # El directorio falso va delante del PATH real en vez de reemplazarlo: los
    # binarios de un bash para Windows arrastran DLLs de su propia carpeta y
    # copiarlos sueltos no funciona.
    ruta = str(path_falso) + os.pathsep + os.environ.get("PATH", "")
    # El tamaño de la ventana sale de un archivo que en la Pi si existe: se
    # apunta a uno inexistente para que los tests den igual aqui que alli, y
    # el que lo prueba de verdad lo sobreescribe.
    entorno = dict(os.environ, PATH=ruta, SMARTMETER_HTTP_PORT="1",
                   SMARTMETER_KIOSK_WAIT="1",
                   SMARTMETER_FB_SIZE_FILE=str(path_falso / "sin-fb"))
    entorno.pop("DISPLAY", None)
    entorno.pop("WAYLAND_DISPLAY", None)
    entorno.update(extra_env or {})
    return subprocess.run(
        ["bash", KIOSK], capture_output=True, text=True, timeout=120, env=entorno
    )


def falso(directorio, nombre, cuerpo="exit 0"):
    ruta = directorio / nombre
    ruta.write_text(f"#!/bin/sh\n{cuerpo}\n", newline="\n")
    ruta.chmod(0o755)
    return ruta


@pytest.fixture
def bin_falso(tmp_path):
    directorio = tmp_path / "bin"
    directorio.mkdir()
    return directorio


def test_sintaxis():
    assert subprocess.run(["bash", "-n", KIOSK]).returncode == 0


@pytest.mark.skipif(
    shutil.which("chromium") or shutil.which("chromium-browser"),
    reason="hay un chromium real en el PATH",
)
def test_falla_con_un_mensaje_claro_si_no_hay_navegador(bin_falso):
    resultado = correr(bin_falso)
    assert resultado.returncode == 1
    assert "no hay chromium instalado" in resultado.stdout


def test_acepta_el_binario_llamado_chromium_browser(bin_falso, tmp_path):
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium-browser", f'echo "$@" > "{marca}"')
    assert correr(bin_falso).returncode == 0
    assert "--kiosk" in marca.read_text()
    assert "http://localhost:1" in marca.read_text()


def test_acepta_el_binario_llamado_chromium(bin_falso, tmp_path):
    """El nombre que trae Bookworm. Fijar uno solo dejaba la pantalla muda."""
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium", f'echo "$@" > "{marca}"')
    assert correr(bin_falso).returncode == 0
    assert "--kiosk" in marca.read_text()


def test_abre_igual_si_el_dashboard_no_responde(bin_falso, tmp_path):
    """Mejor una pagina de error visible que una pantalla negra callada.

    El puerto 1 no escucha, asi que la espera se agota entera.
    """
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium", f'echo "$@" > "{marca}"')
    resultado = correr(bin_falso)
    assert "no respondio" in resultado.stdout
    assert marca.exists()


def test_anota_la_sesion_y_las_pantallas_encontradas(bin_falso, tmp_path):
    """Una pantalla negra sin rastro en el log es lo que no se puede depurar."""
    falso(bin_falso, "chromium")
    resultado = correr(bin_falso, {"DISPLAY": ":0"})
    assert "sesion=:0" in resultado.stdout
    assert "pantallas=" in resultado.stdout


def test_no_toca_el_llavero(bin_falso, tmp_path):
    """Con autologin nadie escribe una contrasena y el llavero de login queda
    cerrado: sin este flag gnome-keyring pide abrirlo tapando el dashboard en
    cada arranque."""
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium", f'echo "$@" > "{marca}"')
    assert correr(bin_falso).returncode == 0
    assert "--password-store=basic" in marca.read_text()


def test_ventana_del_tamano_de_la_pantalla(bin_falso, tmp_path):
    """Chromium no hace ventanas de navegador de menos de ~400 px ni en modo
    kiosco: en la TFT de 320 dibujaba mas ancho que la pantalla y se perdia un
    quinto por la derecha. Una ventana --app si acepta el tamaño pedido."""
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium", f'echo "$@" > "{marca}"')
    fb = tmp_path / "virtual_size"
    fb.write_text("320,480", newline="\n")
    assert correr(bin_falso, {"SMARTMETER_FB_SIZE_FILE": str(fb)}).returncode == 0
    args = marca.read_text()
    assert "--window-size=320,480" in args
    assert "--app=http://localhost:1" in args
    assert "--kiosk" not in args


def test_vuelve_al_kiosco_si_no_hay_tamano(bin_falso, tmp_path):
    """En un HDMI normal o si el framebuffer no dice su tamaño, el modo kiosco
    de siempre: ahi el minimo de Chromium no estorba."""
    marca = tmp_path / "abierto.txt"
    falso(bin_falso, "chromium", f'echo "$@" > "{marca}"')
    resultado = correr(bin_falso, {"SMARTMETER_FB_SIZE_FILE": str(tmp_path / "no-existe")})
    assert resultado.returncode == 0
    assert "--kiosk" in marca.read_text()
