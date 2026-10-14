FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY app/native/id3parse.c /build/id3parse.c
RUN gcc -O2 -fno-stack-protector -o /build/id3parse /build/id3parse.c
RUN mkdir -p /build/vendor \
    && curl --fail --location --retry 3 https://unpkg.com/three@0.160.0/build/three.module.js -o /build/vendor/three.module.js \
    && curl --fail --location --retry 3 https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js -o /build/vendor/OrbitControls.js \
    && sed -i "s@from 'three'@from '/static/vendor/three.module.js'@" /build/vendor/OrbitControls.js

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
# PyYAML 5.3.1 needs pre-Cython-3 build support on Python 3.12.
RUN pip install --no-cache-dir 'Cython<3' \
    && pip install --no-cache-dir -r /app/requirements.txt
COPY app /app
COPY --from=build /build/id3parse /app/native/id3parse
COPY --from=build /build/vendor /app/static/vendor
RUN groupadd -r ecu_engine && useradd -r -g ecu_engine ecu_engine \
    && groupadd -r ecu_abs && useradd -r -g ecu_abs ecu_abs \
    && groupadd -r ecu_autopilot && useradd -r -g ecu_autopilot ecu_autopilot \
    && groupadd -r ecu_telemetry && useradd -r -g ecu_telemetry ecu_telemetry \
    && groupadd -r ecu_infotainment && useradd -r -g ecu_infotainment ecu_infotainment \
    && mkdir -p /app/stores/autopilot /app/stores/abs /app/stores/telemetry /app/stores/infotainment \
    && chown ecu_autopilot:ecu_autopilot /app/stores/autopilot \
    && chown ecu_abs:ecu_abs /app/stores/abs \
    && chown ecu_telemetry:ecu_telemetry /app/stores/telemetry \
    && chown ecu_infotainment:ecu_infotainment /app/stores/infotainment \
    && chmod 700 /app/stores/autopilot /app/stores/abs /app/stores/telemetry /app/stores/infotainment
ENV PYTHONUNBUFFERED=1 \
    STORE_ROOT=/app/stores
EXPOSE 8080 8888 31337
CMD ["python", "-u", "main.py"]
