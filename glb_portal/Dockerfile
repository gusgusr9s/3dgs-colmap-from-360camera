FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GLB_PORTAL_DATA=/data

WORKDIR /app
COPY server.py index.html app.js viewer.js style.css project.css ./
COPY vendor/three ./vendor/three

RUN groupadd --gid 10001 portal && \
    useradd --uid 10001 --gid portal --no-create-home --shell /usr/sbin/nologin portal && \
    mkdir -p /data && chown portal:portal /data

USER portal
EXPOSE 8088
CMD ["python", "server.py", "serve", "--host", "0.0.0.0", "--port", "8088"]
