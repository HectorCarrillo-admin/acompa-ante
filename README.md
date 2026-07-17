# Acompañante Mayor

Bot modular de Telegram para adultos mayores, listo para Railway.

## Módulos

- Medicamentos y tomas diarias
- Alarmas automáticas
- Agenda
- Contactos y emergencia
- Noticias por categorías
- Voz con faster-whisper
- Respuestas en audio con gTTS
- Acceso por Chat ID

## Noticias

Cada solicitud consulta el RSS en ese momento. Los feeds predeterminados usan
Google News RSS con filtros temporales (`when:1d`, `when:2d` o `when:7d`).

Categorías:

- Colombia
- Mundo
- Salud
- Deportes
- Tecnología
- Economía
- Buenas noticias

Comandos:

- Noticias
- Noticias de Colombia
- Noticias del mundo
- Noticias de salud
- Noticias de deportes
- Noticias de tecnología
- Noticias de economía
- Buenas noticias

## Railway

1. Conecta el repositorio.
2. Crea las variables usando `.env.example`.
3. Añade un volumen persistente montado en `/data`.
4. Railway detectará el `Dockerfile`.

## Variables mínimas

```text
TELEGRAM_TOKEN=...
USUARIOS_AUTORIZADOS=1532627802
NOMBRE_USUARIO=Nubia
DB_PATH=/data/acompanante.db
ZONA_HORARIA=America/Bogota
MODELO_VOZ=base
WHISPER_CPU_THREADS=2
WHISPER_BEAM_SIZE=3
```

## Seguridad

`/id` es universal. Las demás funciones solo están disponibles para los IDs
incluidos en `USUARIOS_AUTORIZADOS`.
