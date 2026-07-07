# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-bookworm

ARG APT_MIRROR=mirrors.aliyun.com
ARG UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG UV_CONCURRENT_DOWNLOADS=8
ARG UV_CONCURRENT_BUILDS=4

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=${UV_INDEX_URL} \
    UV_INDEX_URL=${UV_INDEX_URL} \
    UV_LINK_MODE=copy \
    UV_CONCURRENT_DOWNLOADS=${UV_CONCURRENT_DOWNLOADS} \
    UV_CONCURRENT_BUILDS=${UV_CONCURRENT_BUILDS} \
    NPM_CONFIG_REGISTRY=${NPM_REGISTRY} \
    PATH=/opt/venv/bin:/opt/minicook/frontend/node_modules/.bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /workspace

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s#deb.debian.org#${APT_MIRROR}#g; s#security.debian.org#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      bash \
      build-essential \
      ca-certificates \
      curl \
      git \
      nodejs \
      npm \
      openssh-client; \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip uv -i "${UV_INDEX_URL}"

COPY requirements.txt /tmp/minicook/requirements.txt
COPY frontend/package.json frontend/package-lock.json /tmp/minicook/frontend/
COPY docker/docker-entrypoint.sh /usr/local/bin/minicook-entrypoint

RUN set -eux; \
    chmod +x /usr/local/bin/minicook-entrypoint; \
    mkdir -p /opt/minicook/frontend; \
    cp /tmp/minicook/frontend/package.json /tmp/minicook/frontend/package-lock.json /opt/minicook/frontend/; \
    ( \
      /opt/venv/bin/uv pip install \
        --python /opt/venv/bin/python \
        --index-url "${UV_INDEX_URL}" \
        --upgrade \
        -r /tmp/minicook/requirements.txt \
    ) & \
    backend_pid="$!"; \
    ( \
      cd /opt/minicook/frontend \
      && npm ci \
        --registry "${NPM_REGISTRY}" \
        --prefer-offline \
        --no-audit \
        --fund=false \
    ) & \
    frontend_pid="$!"; \
    wait "$backend_pid"; \
    wait "$frontend_pid"; \
    rm -rf /root/.cache /tmp/minicook

EXPOSE 8000 5173

ENTRYPOINT ["minicook-entrypoint"]
CMD ["bash"]
