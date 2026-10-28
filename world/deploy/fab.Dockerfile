FROM snr-unreal:5.8.2-ubuntu24.04
USER root
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
