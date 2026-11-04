FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c

# UE's bundled .NET/native build tools require a newer glibc than Debian 11.
# The engine, project, and GPU driver stay on the host; this image supplies userspace.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o APT::Update::Error-Mode=any update && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
    build-essential ca-certificates curl file git python3 rsync unzip \
    libicu74 libssl3 libcurl4t64 libatomic1 zlib1g \
    libx11-6 libxrandr2 libxi6 libxinerama1 libxcursor1 libxcomposite1 \
    libxdamage1 libxfixes3 libxrender1 libxext6 libxss1 \
    libasound2t64 libgbm1 libvulkan1 libglib2.0-0t64 libnss3 libnspr4 \
    libfontconfig1 libfreetype6 libpng16-16t64 libsdl2-2.0-0 libtbb12 \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o APT::Update::Error-Mode=any update && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
    libatk1.0-0t64 libatk-bridge2.0-0t64 libpango-1.0-0 libcairo2 libatspi2.0-0t64 \
    libegl1 libgl1 vulkan-tools \
    && rm -rf /var/lib/apt/lists/*
ARG SNR_UID=1003
ARG SNR_GID=1004
RUN groupadd --gid ${SNR_GID} snr && useradd --uid ${SNR_UID} --gid ${SNR_GID} --create-home snr
USER snr
WORKDIR /workspace
