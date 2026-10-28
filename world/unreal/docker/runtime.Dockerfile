# Ubuntu 24.04 supplies the runtime libraries required by the packaged UE 5.8.2 build.
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libicu74 libssl3t64 libcurl4t64 libatomic1 zlib1g \
    libx11-6 libxrandr2 libxi6 libxinerama1 libxcursor1 libxcomposite1 \
    libxdamage1 libxfixes3 libxrender1 libxext6 libxss1 libasound2t64 \
    libgbm1 libvulkan1 libglib2.0-0t64 libnss3 libnspr4 \
    libfontconfig1 libfreetype6 libpng16-16t64 libsdl2-2.0-0 libtbb12 \
    libatk1.0-0t64 libatk-bridge2.0-0t64 libpango-1.0-0 libcairo2 libatspi2.0-0t64 \
    libegl1 libgl1 vulkan-tools \
    && rm -rf /var/lib/apt/lists/*
ARG SNR_UID=1002
ARG SNR_GID=1003
RUN groupadd --gid "$SNR_GID" snr && useradd --uid "$SNR_UID" --gid "$SNR_GID" --create-home snr
ENV HOME=/home/snr
USER snr
WORKDIR /workspace
