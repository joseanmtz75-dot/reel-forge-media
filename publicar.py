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

# OJO: camino de FACEBOOK, no el de Instagram.
#
# El de Instagram (graph.instagram.com) publicaba bien las fotos, pero NO tiene
# la API de audio: 'ig_audio' responde "does not exist". Sin audio en tendencia
# un reel no reparte, y los reels son lo unico que reparte en esta cuenta:
# 1052 vistas en 6 reels contra 37 en 17 publicaciones de foto.
#
# De propina, el token de PAGINA no caduca. El de Instagram habia que renovarlo
# cada 60 dias, y eso en una tarea automatica se olvida y falla en silencio.
API = "https://graph.facebook.com/v25.0"

# Mexico no usa horario de verano desde 2022, asi que el desfase es fijo.
ZONA_MEXICO = timezone(timedelta(hours=-6))

# Un video tarda mucho mas en procesarse que una foto, y publicarlo antes de
# que termine falla.
ESPERA_MAXIMA = 180
ESPERA_MAXIMA_VIDEO = 600
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


# =============================================================================
# Audio en tendencia
# =============================================================================

def audio_en_tendencia(user_id: str, token: str, tipo: str = "music") -> list[dict]:
    """
    Lo que Instagram considera en tendencia AHORA MISMO.

    Se pide EN EL MOMENTO DE PUBLICAR, no al aprobar. Las tendencias cambian en
    dias: una cancion elegida el domingo estaria muerta el jueves, y publicar
    con audio viejo es casi tan malo como publicar sin audio.

    Dos cosas que costaron descubrir y que no conviene "arreglar":
      - La respuesta viene en la clave 'audio', NO en 'data' como el resto del
        grafo. Leyendola mal salen cero pistas con el catalogo lleno.
      - NO se manda 'search_query'. Buscando devuelve relleno de libreria
        generica; sin pedir nada devuelve las tendencias de verdad, ademas
        localizadas.
    """
    d = _pedir("GET", "ig_audio", {"audio_type": tipo, "user_id": user_id,
                                   "access_token": token})
    return d.get("audio", [])


LETRAS = {"albur": "a", "giro": "g", "coqueto": "c", "romantico": "r"}


def elegir_audio(user_id: str, token: str, usados: list[str],
                 mood: str = "") -> dict | None:
    """
    La cancion en tendencia que le queda a esta categoria y no se repite.

    El emparejado NO se calcula aqui: viene hecho en musica.json, que se
    clasifica con IA en la maquina del usuario y viaja con el repositorio. Asi
    esta tarea no necesita otra llave en los secretos ni gasta un centimo por
    publicacion — y como el caracter de una cancion no cambia, clasificarla una
    vez vale para siempre.

    Sin musica.json no se rompe nada: rota sin emparejar, que es como estaba
    antes. Y si ya se usaron todas, recicla: mejor repetir que salir mudo.
    """
    try:
        pistas = audio_en_tendencia(user_id, token)
    except RuntimeError as e:
        print(f"    [aviso] no pude consultar el audio: {e}")
        return None
    if not pistas:
        return None

    recientes = set(usados[-8:])
    libres = [p for p in pistas if p.get("audio_id") not in recientes] or pistas

    clasificacion = leer(RAIZ / "musica.json", {})
    letra = LETRAS.get(mood, "")
    if clasificacion and letra:
        encajan = [p for p in libres
                   if letra in (clasificacion.get(p.get("audio_id", ""), {})
                                .get("cat", ""))]
        if encajan:
            return encajan[0]
        print(f"    [aviso] ninguna cancion en tendencia encaja con '{mood}'; "
              f"va la siguiente libre")
    elif not clasificacion:
        print("    [aviso] sin musica.json: no hay emparejado, solo rotacion")

    return libres[0]


def crear_contenedor_reel(user_id: str, token: str, url_video: str,
                          caption: str, audio: dict | None) -> str:
    """
    Un reel, con la cancion en tendencia pegada si la hay.

    video_volume=0 porque nuestros videos llevan una pista MUDA a proposito:
    la musica de Instagram tiene que sonar sola. La pista muda existe solo
    porque no esta claro que Instagram acepte un video sin stream de audio.
    """
    datos = {"media_type": "REELS", "video_url": url_video,
             "caption": caption, "access_token": token}
    if audio and audio.get("audio_id"):
        datos["audio_configuration"] = json.dumps({
            "audio_id": audio["audio_id"],
            "audio_volume": 100,
            "video_volume": 0,
        })
    return _pedir("POST", f"{user_id}/media", datos)["id"]


def esperar_listo(contenedor: str, token: str, espera: int = ESPERA_MAXIMA) -> None:
    """
    Instagram procesa en segundo plano. Publicar antes de que termine falla,
    asi que hay que preguntar hasta que diga FINISHED.

    El video necesita mucho mas tiempo que la foto: por eso 'espera' se sube a
    ESPERA_MAXIMA_VIDEO cuando la pieza es un reel.
    """
    limite = time.time() + espera
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


def publicar_reel(user_id: str, token: str, url_video: str, caption: str,
                  audio: dict | None) -> str:
    contenedor = crear_contenedor_reel(user_id, token, url_video, caption, audio)
    esperar_listo(contenedor, token, ESPERA_MAXIMA_VIDEO)
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
    # Camino de Facebook. Los nombres viejos se aceptan de reserva para que una
    # corrida no muera si todavia no se han cambiado los secretos, pero SIN los
    # de Facebook no hay audio en tendencia: el endpoint no existe del otro lado.
    token = (os.environ.get("FB_PAGE_TOKEN", "").strip()
             or os.environ.get("IG_ACCESS_TOKEN", "").strip())
    user_id = (os.environ.get("FB_IG_USER_ID", "").strip()
               or os.environ.get("IG_USER_ID", "").strip())
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    rama = os.environ.get("GITHUB_REF_NAME", "main").strip()
    forzar = os.environ.get("FORZAR_PIEZA", "").strip()

    if not token or not user_id:
        print("[ERROR] Faltan FB_PAGE_TOKEN o FB_IG_USER_ID en los secretos.")
        return 1
    if not os.environ.get("FB_PAGE_TOKEN", "").strip():
        print("[AVISO] Usando el token viejo de Instagram. Los reels saldran "
              "SIN musica: la API de audio no existe por ese camino.")

    base_url = f"https://raw.githubusercontent.com/{repo}/{rama}/imagenes"
    hoy = hoy_en_mexico()
    print(f"Hoy en Mexico: {hoy}  (en UTC seria {datetime.now(timezone.utc).date()})")

    calendario = leer(CALENDARIO, {"piezas": []})
    registro = leer(PUBLICADO, {"publicadas": []})
    ya_salieron = {p["id"] for p in registro["publicadas"]}

    modo = os.environ.get("MODO", "hoy").strip() or "hoy"

    if forzar:
        pendientes = [p for p in calendario["piezas"] if p["id"] == forzar]
        print(f"Modo forzado: se busca la pieza '{forzar}'")

    elif modo == "atrasadas":
        # Rescate: piezas de dias ANTERIORES que nunca salieron.
        #
        # Hace falta porque GitHub no siempre ejecuta la tarea: hubo dias en
        # que sencillamente no corrio, y esas piezas se quedaban muertas en el
        # calendario para siempre porque solo se miraba la fecha de hoy.
        #
        # Se excluye la de HOY a proposito: esa tiene su hora elegida y debe
        # salir en su ventana, no a cualquier hora que se le ocurra al rescate.
        atrasadas = sorted(
            (p for p in calendario["piezas"]
             if p["fecha"] < hoy and p["id"] not in ya_salieron),
            key=lambda p: p["fecha"])
        if not atrasadas:
            print("No hay nada atrasado. Todo al dia.")
            return 0
        # De una en una: soltar cinco de golpe seria peor que el atraso
        pendientes = atrasadas[:1]
        print(f"{len(atrasadas)} pieza(s) atrasadas. Se publica la mas vieja "
              f"({pendientes[0]['fecha']}); el resto en las proximas corridas.")

    else:
        pendientes = [p for p in calendario["piezas"]
                      if p["fecha"] == hoy and p["id"] not in ya_salieron]

    if not pendientes:
        atrasadas = [p for p in calendario["piezas"]
                     if p["fecha"] < hoy and p["id"] not in ya_salieron]
        print("No hay nada programado para hoy. Todo en orden."
              + (f"  (ojo: {len(atrasadas)} atrasada(s) esperando el rescate)"
                 if atrasadas else ""))
        return 0

    # Que canciones se usaron ultimamente, para no repetirlas
    usados = [p.get("audio_id") for p in registro["publicadas"] if p.get("audio_id")]

    fallos = 0
    for pieza in pendientes:
        etiqueta = f"{pieza['id']} ({pieza.get('formato','?')}, {pieza.get('mood','?')})"
        print(f"\n>>> Publicando {etiqueta}")
        try:
            urls = [f"{base_url}/{a}" for a in pieza["archivos"]]
            for u in urls:
                print(f"    {u}")

            audio = None
            formato = pieza.get("formato")
            es_reel = formato == "reel" or urls[0].lower().endswith(".mp4")

            if es_reel:
                audio = elegir_audio(user_id, token, usados,
                                     pieza.get("mood", ""))
                if audio:
                    print(f"    audio: {audio.get('title')} — "
                          f"{audio.get('display_artist') or audio.get('ig_username')}")
                else:
                    print("    audio: NINGUNO (saldra mudo)")
                media_id = publicar_reel(user_id, token, urls[0],
                                         pieza.get("caption", ""), audio)
            elif formato == "carrusel" and len(urls) > 1:
                media_id = publicar_carrusel(user_id, token, urls, pieza.get("caption", ""))
            else:
                media_id = publicar_suelta(user_id, token, urls[0], pieza.get("caption", ""))

            print(f"    PUBLICADO  media_id={media_id}")
            anotacion = {
                "id": pieza["id"], "media_id": media_id,
                "fecha_programada": pieza["fecha"],
                "publicado_en": datetime.now(ZONA_MEXICO).isoformat(timespec="seconds"),
                "formato": formato, "mood": pieza.get("mood"),
                "texto": pieza.get("texto", ""),
            }
            if audio:
                # Se anota para no repetir cancion y para poder cruzar despues
                # que audios rindieron mejor.
                anotacion["audio_id"] = audio.get("audio_id")
                anotacion["audio_titulo"] = audio.get("title")
                usados.append(audio.get("audio_id"))
            registro["publicadas"].append(anotacion)
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
