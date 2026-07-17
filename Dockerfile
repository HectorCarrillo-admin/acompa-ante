FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --upgrade \
    "pip==24.3.1" \
    "setuptools==69.5.1" \
    wheel \
    && python -m pip install --no-build-isolation -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
