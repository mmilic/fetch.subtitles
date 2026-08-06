FROM python:3.13-slim

# ca-certificates: subliminal's providers talk to their APIs over HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch_subtitles.py .

RUN useradd --create-home --uid 1000 fetcher
USER fetcher

# Mount your video library here, e.g.:
#   docker run --rm -v /path/to/library:/videos fetch-subtitles -l en -l sr
VOLUME ["/videos"]

ENTRYPOINT ["python", "fetch_subtitles.py"]
CMD ["/videos"]
