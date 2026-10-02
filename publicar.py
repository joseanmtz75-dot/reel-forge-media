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
MUSICA = RAIZ / "musica.json"

# Una cancion "va subiendo" mientras lleve 2 dias o menos apareciendo en
# la lista de tendencia. Pasado eso ya se aplano.
DIAS_NUEVA = 2

INSTRUCCION_DJ = """Clasifica canciones para reels de una cuenta mexicana de frases.

Categorias:
a = albur (doble sentido, picardia) -> corridos tumbados, reggaeton, cumbia con actitud
g = giro (empieza serio y remata en broma) -> algo epico o solemne, que contraste
c = coqueto (ligue, juguetón) -> pop, reggaeton suave, algo animado
r = romantico (sincero, sin broma) -> balada, romantica, piano, banda sentimental

Para cada cancion responde las letras que le queden (1 a 3, sin espacios).
Si no le queda ninguna, responde "-".

Responde SOLO un JSON {"numero":"letras"}. Sin texto adicional."""


def clasificar_nuevas(pistas: list[dict]) -> dict:
    """
    Clasifica las canciones en tendencia que aun no estuvieran en musica.json.

    POR QUE ESTO VIVE AQUI Y NO SOLO EN LA MAQUINA DEL USUARIO
    ----------------------------------------------------------
    La clasificacion se hacia solo en local y viajaba como archivo. Funciono
    cuatro dias: la lista de tendencias de Instagram ROTO COMPLETA en ocho, y
    desde el 23 de septiembre el emparejado dejo de funcionar sin que nada lo
    dijera — la nube seguia publicando, pero eligiendo la primera cancion libre
    en vez de la que le pegaba a la frase.

    Un cache que caduca y nadie refresca es lo mismo que no tener cache. Asi
    que se refresca aqui, donde si corre todos los dias.

    Cuesta una fraccion de centimo por rotacion (25 canciones = $0.0002) y solo
    se paga cuando aparecen canciones nuevas. Si falta la llave o DeepSeek
    falla, NO se rompe nada: se sigue rotando sin emparejar, que es como estaba.
    """
    clave = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    cache = leer(MUSICA, {})
    nuevas = [p for p in pistas
              if p.get("audio_id") and p["audio_id"] not in cache]
    if not nuevas:
        return cache
    if not clave:
        print(f"    [aviso] {len(nuevas)} cancion(es) sin clasificar y no hay "
              f"DEEPSEEK_API_KEY: se rotara sin emparejar")
        return cache

    listado = "\n".join(
        f"{i+1}. {p.get('title','?')} - "
        f"{p.get('display_artist') or p.get('ig_username') or ''}".strip(" -")
        for i, p in enumerate(nuevas))
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {clave}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-flash",
                  "messages": [{"role": "system", "content": INSTRUCCION_DJ},
                               {"role": "user", "content": listado}],
                  "temperature": 0.1, "max_tokens": 900,
                  "response_format": {"type": "json_object"},
                  # Sin esto gasta el tope entero razonando y devuelve vacio.
                  "thinking": {"type": "disabled"}},
            timeout=120)
        d = r.json()
        mapa = json.loads(d["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"    [aviso] no pude clasificar ({e}); se rota sin emparejar")
        return cache

    hoy = hoy_en_mexico()
    for i, p in enumerate(nuevas):
        letras = str(mapa.get(str(i + 1), "-")).lower()
        cache[p["audio_id"]] = {
            "cat": "".join(sorted(set(c for c in letras if c in "agcr"))),
            "titulo": p.get("title", "?"),
            # El dia que la cancion aparecio por PRIMERA VEZ en tendencia. La
            # API no dice si un audio va subiendo o ya se aplano; esto es lo mas
            # cerca que se puede estar de medirlo, y solo funciona porque esta
            # tarea corre todos los dias.
            "visto_en": hoy,
        }
    escribir(MUSICA, cache)
    uso = d.get("usage", {})
    print(f"    {len(nuevas)} cancion(es) nuevas clasificadas "
          f"({uso.get('prompt_tokens','?')} + {uso.get('completion_tokens','?')} tokens)")
    return cache


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

    # Se refresca ANTES de elegir: si la tendencia trajo canciones nuevas, hoy
    # mismo ya se emparejan bien en vez de esperar a que alguien lo note.
    cache = clasificar_nuevas(pistas)
    if not cache:
        print("    [aviso] sin musica.json: no hay emparejado, solo rotacion")
        return libres[0]

    letra = LETRAS.get(mood, "")
    frontera = (datetime.now(ZONA_MEXICO) - timedelta(days=DIAS_NUEVA)).date().isoformat()

    def encaja(p):
        return letra and letra in cache.get(p.get("audio_id", ""), {}).get("cat", "")

    def nueva(p):
        return cache.get(p.get("audio_id", ""), {}).get("visto_en", "") >= frontera

    # ORDEN DE PREFERENCIA, y el segundo puesto es una APUESTA:
    #
    #   1. encaja con la categoria Y acaba de aparecer
    #   2. acaba de aparecer, aunque no encaje          <- la apuesta
    #   3. encaja con la categoria
    #   4. cualquiera libre
    #
    # El unico reel que rompio el techo (1622 vistas, 37 compartidos, contra un
    # maximo previo de 257 y 2 compartidos en TODO el proyecto) llevaba una
    # cancion MAL emparejada que a los dos dias ya habia rotado fuera de la
    # tendencia: un audio subiendo rapido.
    #
    # Y cuadra con el diagnostico: a esta cuenta no le falta retencion (62-83%),
    # le falta REPARTO. Un audio que sube es una apuesta de distribucion; uno que
    # encaja es una de ajuste. Falta la primera.
    #
    # Es n=1. Si sale falso, invertir el orden son dos lineas.
    for nombre, criterio in (("encaja y es nueva", lambda p: encaja(p) and nueva(p)),
                             ("es nueva", nueva),
                             ("encaja", encaja)):
        candidatas = [p for p in libres if criterio(p)]
        if candidatas:
            print(f"    criterio: {nombre}")
            return candidatas[0]

    print(f"    [aviso] ninguna encaja ni es nueva; va la siguiente libre")
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
