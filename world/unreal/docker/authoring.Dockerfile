FROM snr-unreal:5.8.2-ubuntu24.04
USER root
RUN apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o APT::Update::Error-Mode=any update && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
    xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
USER snr
