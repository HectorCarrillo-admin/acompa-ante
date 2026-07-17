from __future__ import annotations

import logging

import requests

from app.config import Settings

logger = logging.getLogger(__name__)


class WeatherService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def summary(self) -> str:
        if (
            self.settings.latitud_clima is None
            or self.settings.longitud_clima is None
        ):
            return (
                "El clima todavía no está configurado. "
                "Agrega LATITUD_CLIMA y LONGITUD_CLIMA en Railway."
            )

        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": self.settings.latitud_clima,
                    "longitude": self.settings.longitud_clima,
                    "current": "temperature_2m,apparent_temperature,precipitation,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": self.settings.timezone_name,
                    "forecast_days": 1,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            daily = data.get("daily", {})

            temp = current.get("temperature_2m")
            apparent = current.get("apparent_temperature")
            rain = current.get("precipitation", 0)
            max_temp = (daily.get("temperature_2m_max") or [None])[0]
            min_temp = (daily.get("temperature_2m_min") or [None])[0]
            rain_prob = (
                daily.get("precipitation_probability_max") or [None]
            )[0]

            return (
                f"Clima actual en {self.settings.ciudad_clima}: "
                f"{temp} grados, sensación térmica de {apparent} grados. "
                f"Temperatura máxima de hoy {max_temp} grados y mínima de "
                f"{min_temp} grados. Probabilidad máxima de lluvia: "
                f"{rain_prob} por ciento. Precipitación actual: {rain} milímetros."
            )
        except Exception as exc:
            logger.exception("Error consultando clima: %s", exc)
            return "No pude consultar el clima en este momento."
