FROM archlinux:latest

RUN pacman -Syu --noconfirm \
      base-devel cmake git ffmpeg openssh \
      vulkan-radeon vulkan-icd-loader vulkan-headers vulkan-tools shaderc spirv-headers \
    && rm -rf /var/cache/pacman/pkg/* 

# whisper.cpp and llama.cpp, both with the Vulkan backend
RUN git clone --depth 1 https://github.com/ggml-org/whisper.cpp /opt/whisper.cpp \
    && cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build -DGGML_VULKAN=1 \
    && cmake --build /opt/whisper.cpp/build -j --config Release

RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp /opt/llama.cpp \
    && cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DGGML_VULKAN=1 \
    && cmake --build /opt/llama.cpp/build -j --config Release

ARG USERNAME=dev
ARG USER_UID=1000
RUN useradd -m -u ${USER_UID} -G video -s /bin/bash ${USERNAME}

USER ${USERNAME}

RUN curl -fsSL https://mise.run | sh
ENV PATH="/home/${USERNAME}/.local/bin:${PATH}"
