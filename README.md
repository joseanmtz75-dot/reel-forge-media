# reel-forge-media

Aquí esperan las imágenes que se van a publicar en Instagram, y aquí vive la
tarea que las publica sola a su hora.

Es público **solo porque tiene que serlo**: Instagram necesita descargar cada
imagen desde una dirección de internet accesible. El código del proyecto y el
banco de frases están en otro repositorio, privado.

## Qué hay

    imagenes/            las piezas que esperan su turno
    calendario.json      qué se publica cada día y a qué hora
    publicado.json       lo que ya salió, para que la app local se entere
    .github/workflows/   la tarea programada

Las imágenes se borran después de publicarse, así que aquí nunca hay más de
una semana de contenido.

## Horarios

Definidos en hora de México (UTC−6, sin horario de verano). En el archivo de la
tarea van en UTC, que es lo que entiende GitHub.

| Día | Hora local | Ventana objetivo |
|---|---|---|
| Lunes | 12:15 | 12:00–14:00 |
| Martes | 13:30 | 13:00–17:00 |
| Miércoles | 12:30 | 12:00–18:00 |
| Jueves | 18:15 | 18:00–21:00 |
| Viernes | 12:15 | 12:00–14:00 |
| Sábado | 10:30 | 10:00–13:00 |
| Domingo | 19:15 | 19:00–21:00 |

Cada hora está al **inicio** de su ventana a propósito: GitHub puede retrasar
una tarea entre 5 y 30 minutos, y así el retraso sigue cayendo dentro de la
franja buena.
