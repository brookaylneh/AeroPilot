"""Always-warm Modal Servers for the provider-neutral model clients."""

import subprocess
from pathlib import Path

import modal

if modal.is_local():
    from mission_planner.environment import load_env

    load_env()

app = modal.App("snr")
weights = modal.Volume.from_name("snr-weights")
LLAMA_COMMIT = "3c809f44383bcebc318b8cddd8c6d11aaa861e9b"
SAM_COMMIT = "f1647e8d0c1d280991f4d1b7d974c2704537fe66"
DIRECTORY = Path(__file__).parent

glimmer_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu24.04", add_python="3.12")
    .env({"PIP_RETRIES": "0"})
    .apt_install("build-essential", "cmake", "git", "libssl-dev")
    .run_commands(
        "git init /llama.cpp",
        "git -C /llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git",
        f"git -C /llama.cpp fetch --depth 1 origin {LLAMA_COMMIT}",
        "git -C /llama.cpp checkout --detach FETCH_HEAD",
        f"test $(git -C /llama.cpp rev-parse HEAD) = {LLAMA_COMMIT}",
    )
    .add_local_file(DIRECTORY / "llama-ready.patch", "/llama-ready.patch", copy=True)
    .run_commands(
        "git -C /llama.cpp apply --check /llama-ready.patch",
        "git -C /llama.cpp apply /llama-ready.patch",
        "cmake -S /llama.cpp -B /llama.cpp/build -DGGML_CUDA=ON "
        "-DCMAKE_CUDA_ARCHITECTURES=90 -DGGML_NATIVE=OFF -DLLAMA_BUILD_TESTS=OFF "
        "-DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON "
        "-DLLAMA_OPENSSL=ON -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF "
        "-DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined",
        "cmake --build /llama.cpp/build --target llama-server -j 8",
        "cuobjdump --list-elf /llama.cpp/build/bin/libggml-cuda.so",
        "git -C /llama.cpp rev-parse HEAD",
        "sha256sum /llama.cpp/build/bin/llama-server /llama.cpp/build/bin/libggml-cuda.so",
    )
)

sam_image = (
    modal.Image.debian_slim(python_version="3.12")
    .env({"PIP_RETRIES": "0", "TORCHINDUCTOR_CACHE_DIR": "/tmp/torchinductor"})
    .apt_install("git", "libgl1", "libglib2.0-0")
    .pip_install(
        "torch==2.10.0",
        "torchvision==0.25.0",
        "setuptools==80.10.2",
        "numpy==1.26.4",
        "einops==0.8.2",
        "decord==0.6.0",
        "pycocotools==2.0.11",
        "scipy==1.17.1",
        "pillow==13.0.0",
        "psutil==7.2.2",
        "fastapi==0.136.3",
        "uvicorn==0.54.0",
        "pydantic==2.14.1",
        "timm==1.0.30",
        "ftfy==6.1.1",
        "iopath==0.1.10",
        "huggingface_hub==2.3.0",
        f"git+https://github.com/facebookresearch/sam3.git@{SAM_COMMIT}",
    )
    .run_commands("python -m pip freeze")
    .add_local_file(DIRECTORY.parent / "segmentation.py", "/segmentation.py", copy=True)
    .add_local_file(DIRECTORY / "sam_server.py", "/sam_server.py", copy=True)
    .add_local_file(DIRECTORY / "sam_tracking.py", "/sam_tracking.py", copy=True)
)


@app.server(
    image=glimmer_image,
    gpu="H200",
    volumes={"/weights": weights.read_only()},
    min_containers=1,
    max_concurrency=4,
    routing_region="us-west",
    port=8000,
    startup_timeout=1800,
)
class Glimmer:
    @modal.enter()
    def start(self):
        subprocess.run(["nvidia-smi"], check=True)
        self.process = subprocess.Popen(
            [
                "/llama.cpp/build/bin/llama-server",
                "--model",
                "/weights/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf",
                "--mmproj",
                "/weights/mmproj-Muse-Glimmer-30B-Q4_K_M.gguf",
                "--load-mode",
                "mmap",
                "--alias",
                "muse-glimmer",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--n-gpu-layers",
                "99",
                "--ctx-size",
                "131072",
                "--parallel",
                "4",
                "--kv-unified-per-slot",
                "32768",
                "--jinja",
                "--temp",
                "1.0",
                "--top-p",
                "0.95",
                "--top-k",
                "64",
                "--no-webui",
            ]
        )


@app.server(
    image=sam_image,
    gpu="H100!",
    volumes={"/weights": weights.read_only()},
    min_containers=1,
    max_concurrency=8,
    routing_region="us-west",
    port=8000,
    startup_timeout=1800,
)
class Sam:
    @modal.enter()
    def start(self):
        subprocess.run(["nvidia-smi"], check=True)
        self.process = subprocess.Popen(["python", "/sam_server.py"])
