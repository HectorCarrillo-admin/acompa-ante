# Acompañante Mayor 2.0

Arquitectura modular para Railway.

## Módulos

- `voice.py`: reconocimiento y síntesis de voz.
- `parser.py`: interpretación de comandos.
- `alarms.py`: alarmas y confirmaciones.
- `services/medications.py`: medicamentos y tomas.
- `services/agenda.py`: agenda.
- `services/contacts.py`: contactos.
- `services/news.py`: noticias.
- `services/weather.py`: clima.
- `services/notifications.py`: Twilio y WhatsApp.
- `handlers.py`: Telegram.
- `main.py`: inicialización.

## Noticias

Las noticias se consultan en el momento mediante RSS y se leen en audio.

Categorías:

- Colombia
- Mundo
- Salud
- Deportes
- Tecnología
- Economía
- Buenas noticias

## Clima

Usa Open-Meteo. Configura:

```text
CIUDAD_CLIMA=Bogotá
LATITUD_CLIMA=4.7110
LONGITUD_CLIMA=-74.0721
```

## Railway

1. Sube todos los archivos.
2. Crea las variables de `.env.example`.
3. Monta un volumen en `/data`.
4. Railway construirá con el `Dockerfile`.

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
