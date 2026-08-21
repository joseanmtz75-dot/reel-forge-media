#!/usr/bin/env python3
"""
reel-forge-media / publicar.py
==============================
Publica en Instagram la pieza que toca hoy. Corre en GitHub, no en tu PC.

COMO SABE QUE PUBLICAR
----------------------
No se guia por la hora sino por la FECHA. Lee calendario.json, busca lo que
esta programado para hoy y que todavia no haya salido, y lo publica.

Eso importa porque GitHub puede retrasar una tarea entre 5 y 30 minutos, y
porque dos de los horarios (jueves y domingo por la noche en Mexico) caen ya en
el dia siguiente en UTC. Si el programa se guiara por la hora, esos dos dias
publicarian la pieza equivocada.

Por eso "hoy" siempre se calcula en hora de Mexico, nunca en UTC.

DE DONDE SACA LAS IMAGENES
--------------------------
De este mismo repositorio, por su direccion publica en raw.githubusercontent.
Instagram las descarga de ahi: por eso el repositorio tiene que ser publico.

QUE DEJA ANOTADO
----------------
Escribe en publicado.json lo que salio. La aplicacion local lee ese archivo al
abrirse para enterarse de lo que se publico sin ella.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent
CALENDARIO = RAIZ / "calendario.json"
PUBLICADO = RAIZ / "publicado.json"

API = "https://graph.instagram.com/v21.0"
# Mexico no usa horario de verano desde 2022, asi que el desfase es fijo.
ZONA_MEXICO = timezone(timedelta(hours=-6))

ESPERA_MAXIMA = 180      # segundos esperando a que Instagram procese la pieza
PAUSA_ENTRE_INTENTOS = 6


def hoy_en_mexico() -> str:
    return datetime.now(ZONA_MEXICO).date().isoformat()


def leer(ruta: Path, por_defecto: dict) -> dict:
    if not ruta.exists():
        return por_defecto
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return por_defecto


def escribir(ruta: Path, datos: dict) -> None:
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


# =============================================================================
# Instagram
# =============================================================================

def _pedir(metodo: str, ruta: str, datos: dict) -> dict:
    url = f"{API}/{ruta.lstrip('/')}"
    r = (requests.post(url, data=datos, timeout=60) if metodo == "POST"
         else requests.get(url, params=datos, timeout=60))
    try:
        d = r.json()
    except ValueError:
        raise RuntimeError(f"Respuesta ilegible de Instagram (HTTP {r.status_code})")
    if "error" in d:
        e = d["error"]
        raise RuntimeError(f"{e.get('message')} [codigo {e.get('code')}]")
    return d


def crear_contenedor(user_id: str, token: str, url_imagen: str,
                     caption: str, es_carrusel_hijo: bool = False) -> str:
    datos = {"image_url": url_imagen, "access_token": token}
    if es_carrusel_hijo:
        datos["is_carousel_item"] = "true"
    else:
        datos["caption"] = caption
    return _pedir("POST", f"{user_id}/media", datos)["id"]


def esperar_listo(contenedor: str, token: str) -> None:
    """
    Instagram procesa en segundo plano. Publicar antes de que termine falla,
    asi que hay que preguntar hasta que diga FINISHED.
    """
    limite = time.time() + ESPERA_MAXIMA
    while time.time() < limite:
        estado = _pedir("GET", contenedor,
                        {"fields": "status_code,status", "access_token": token})
        codigo = estado.get("status_code")
        if codigo == "FINISHED":
            return
        if codigo == "ERROR":
            raise RuntimeError(f"Instagram rechazo la pieza: {estado.get('status')}")
        time.sleep(PAUSA_ENTRE_INTENTOS)
    raise RuntimeError("Instagram tardo demasiado en procesar la pieza")


def publicar_suelta(user_id: str, token: str, url_imagen: str, caption: str) -> str:
    contenedor = crear_contenedor(user_id, token, url_imagen, caption)
    esperar_listo(contenedor, token)
    return _pedir("POST", f"{user_id}/media_publish",
                  {"creation_id": contenedor, "access_token": token})["id"]


def publicar_carrusel(user_id: str, token: str, urls: list[str], caption: str) -> str:
    """
    Un carrusel se arma en dos pasos: primero un contenedor por lamina, y
    despues uno que los agrupa. Instagram permite entre 2 y 10 laminas.
    """
    if not 2 <= len(urls) <= 10:
        raise RuntimeError(f"Un carrusel necesita entre 2 y 10 laminas, hay {len(urls)}")

    hijos = []
    for url in urls:
        hijo = crear_contenedor(user_id, token, url, "", es_carrusel_hijo=True)
        esperar_listo(hijo, token)
        hijos.append(hijo)

    padre = _pedir("POST", f"{user_id}/media", {
        "media_type": "CAROUSEL", "children": ",".join(hijos),
        "caption": caption, "access_token": token})["id"]
    esperar_listo(padre, token)
    return _pedir("POST", f"{user_id}/media_publish",
                  {"creation_id": padre, "access_token": token})["id"]


# =============================================================================
# Lo del dia
# =============================================================================

def main() -> int:
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("IG_USER_ID", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    rama = os.environ.get("GITHUB_REF_NAME", "main").strip()
    forzar = os.environ.get("FORZAR_PIEZA", "").strip()

    if not token or not user_id:
        print("[ERROR] Faltan IG_ACCESS_TOKEN o IG_USER_ID en los secretos.")
        return 1

    base_url = f"https://raw.githubusercontent.com/{repo}/{rama}/imagenes"
    hoy = hoy_en_mexico()
    print(f"Hoy en Mexico: {hoy}  (en UTC seria {datetime.now(timezone.utc).date()})")

    calendario = leer(CALENDARIO, {"piezas": []})
    registro = leer(PUBLICADO, {"publicadas": []})
    ya_salieron = {p["id"] for p in registro["publicadas"]}

    if forzar:
        pendientes = [p for p in calendario["piezas"] if p["id"] == forzar]
        print(f"Modo forzado: se busca la pieza '{forzar}'")
    else:
        pendientes = [p for p in calendario["piezas"]
                      if p["fecha"] == hoy and p["id"] not in ya_salieron]

    if not pendientes:
        print("No hay nada programado para hoy. Todo en orden.")
        return 0

    fallos = 0
    for pieza in pendientes:
        etiqueta = f"{pieza['id']} ({pieza.get('formato','?')}, {pieza.get('mood','?')})"
        print(f"\n>>> Publicando {etiqueta}")
        try:
            urls = [f"{base_url}/{a}" for a in pieza["archivos"]]
            for u in urls:
                print(f"    {u}")

            if pieza.get("formato") == "carrusel" and len(urls) > 1:
                media_id = publicar_carrusel(user_id, token, urls, pieza.get("caption", ""))
            else:
                media_id = publicar_suelta(user_id, token, urls[0], pieza.get("caption", ""))

            print(f"    PUBLICADO  media_id={media_id}")
            registro["publicadas"].append({
                "id": pieza["id"], "media_id": media_id,
                "fecha_programada": pieza["fecha"],
                "publicado_en": datetime.now(ZONA_MEXICO).isoformat(timespec="seconds"),
                "formato": pieza.get("formato"), "mood": pieza.get("mood"),
                "texto": pieza.get("texto", ""),
            })
            escribir(PUBLICADO, registro)
        except Exception as e:
            fallos += 1
            print(f"    [ERROR] {e}")

    if fallos:
        print(f"\n{fallos} pieza(s) fallaron.")
        return 1
    print(f"\n{len(pendientes)} pieza(s) publicadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
