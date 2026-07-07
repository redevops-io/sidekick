# Serve a local NVIDIA-CUDA model for sidekick's `--provider cuda` backend.
#
# Target hardware: the **proxmox** box (`ssh proxmox`) with 2× NVIDIA Blackwell GPUs —
# RTX PRO 4500 (32 GB) + RTX PRO 5000 (48 GB) = ~80 GB VRAM — plus 377 GB system RAM.
# Model:
#   nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4   — a 120B-param MoE (~12B active),
#   quantized to **NVFP4** (4-bit FP), which Blackwell accelerates natively.
#
# Exposes an **OpenAI-compatible `/v1` API on :8000**, which is exactly what sidekick's
# `cuda` provider talks to (SIDEKICK_API_BASE / VLLM_BASE_URL=http://<host>:8000/v1).
# (The `serve_vllm.justfile` sibling is the AMD/ROCm Strix-Halo recipe; this is the CUDA one.)
#
#   just -f scripts/serve_cuda.justfile <recipe>
#
# ── VRAM note (why CPU offload) ────────────────────────────────────────────────────────
#  The NVFP4 weights are ~80 GB on disk — essentially equal to total VRAM, leaving no room
#  for the KV cache, activations, and CUDA context. `serve-vllm` therefore spills part of
#  the weights to system RAM via `--cpu-offload-gb` (this box has 377 GB). This trades some
#  throughput for the ability to run the full 120B NVFP4 checkpoint on 80 GB of VRAM.
#  If you have a box with ≥96 GB VRAM, drop CPU_OFFSET to 0 for full-speed GPU-only serving.
# ─────────────────────────────────────────────────────────────────────────────────────────

HF_REPO       := "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"
SERVED_NAME   := "Nemotron-3-Super-120B-A12B"
MODELS_DIR    := env_var_or_default("MODELS_DIR", "/storage/models")
MODEL_PATH    := MODELS_DIR / "NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"
PORT          := "8000"
# Tensor-parallel across both Blackwell GPUs.
TP            := "2"
# Weight bytes to keep in CPU RAM instead of VRAM (per the VRAM note above). 0 = GPU-only.
CPU_OFFSET    := env_var_or_default("CPU_OFFSET", "24")
# Context window. 32k is a safe default for the coding/orchestration prompts sidekick sends.
CTX           := "32768"
# CUDA vLLM image (CUDA-native; bundles its own CUDA runtime — host just needs the driver
# + nvidia-container-toolkit). Pin a Blackwell/NVFP4-capable tag in production.
VLLM_IMAGE    := env_var_or_default("VLLM_IMAGE", "vllm/vllm-openai:latest")

# ── GGUF / llama.cpp path (alternative backend) ────────────────────────────────────────
# A GGUF model served by llama.cpp (CUDA) — native GGUF + MoE, OpenAI /v1, all layers on GPU.
# Used here for `mradermacher/Qwen3.5-122B-A10B-GGUF` (a 122B MoE, ~10B active): an IQ4_XS
# (~66 GB) quant fits the 80 GB GPU pair fully on-GPU (no offload) with room for KV cache, so
# it runs FAST (only the ~10B active path computes) — unlike the 120B NVFP4 CPU-offload path.
# `q4 and up`: IQ4_XS is the largest 4-bit-class quant that still leaves real context room on
# this box (Q4_K_S/Q4_K_M overflow once the KV cache is added).
GGUF_REPO     := "mradermacher/Qwen3.5-122B-A10B-GGUF"
GGUF_FILE     := "Qwen3.5-122B-A10B.IQ4_XS.gguf"
GGUF_NAME     := "Qwen3.5-122B-A10B"
GGUF_DIR      := MODELS_DIR / "Qwen3.5-122B-A10B-GGUF"
# llama.cpp CUDA server image (native GGUF, OpenAI-compatible /v1, supports tools via --jinja).
LLAMACPP_IMAGE := env_var_or_default("LLAMACPP_IMAGE", "ghcr.io/ggml-org/llama.cpp:server-cuda")

# Download the NVFP4 checkpoint from Hugging Face into MODELS_DIR.
fetch:
    mkdir -p "{{MODELS_DIR}}"
    uv tool run --from "huggingface_hub[cli]" hf download \
        "{{HF_REPO}}" \
        --local-dir "{{MODEL_PATH}}"
    @echo "Fetched to {{MODEL_PATH}}"

# Download the single GGUF quant from Hugging Face into GGUF_DIR.
fetch-gguf:
    mkdir -p "{{GGUF_DIR}}"
    uv tool run --from "huggingface_hub[cli]" hf download \
        "{{GGUF_REPO}}" "{{GGUF_FILE}}" \
        --local-dir "{{GGUF_DIR}}"
    @echo "Fetched to {{GGUF_DIR}}/{{GGUF_FILE}}"

# Serve the GGUF via llama.cpp (CUDA). All layers on GPU (-ngl 999), tensor-split across both
# GPUs, OpenAI-compatible /v1 on :8000, tool-calling via the model's chat template (--jinja).
# KV cache is q8-quantized to stretch context within the leftover VRAM. This is the FAST path
# for the 122B-A10B MoE (only ~10B params are active per token).
serve-llamacpp:
    docker rm -f qwen-llamacpp 2>/dev/null || true
    docker run -d --name qwen-llamacpp --restart unless-stopped \
        --gpus all --ipc=host \
        -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
        -p {{PORT}}:{{PORT}} \
        -v "{{MODELS_DIR}}:/models" \
        "{{LLAMACPP_IMAGE}}" \
        -m "/models/Qwen3.5-122B-A10B-GGUF/{{GGUF_FILE}}" \
        --alias "{{GGUF_NAME}}" \
        --host 0.0.0.0 --port {{PORT}} \
        -ngl 999 -c {{CTX}} \
        --cache-type-k q8_0 --cache-type-v q8_0 \
        --jinja \
        --parallel 1
    @echo "started container 'qwen-llamacpp'; follow with: docker logs -f qwen-llamacpp"

# Serve NVFP4 on CUDA via vLLM. Tensor-parallel over both GPUs, partial CPU offload of
# weights so the 120B checkpoint fits alongside the KV cache on 80 GB VRAM. Exposes the
# OpenAI-compatible /v1 API with tool-calling enabled (sidekick's selfhosted loop needs it).
#
# Notes baked in from bringing this up on the proxmox box:
#  • The vllm/vllm-openai image ENTRYPOINT is already `vllm serve`, so the command is just
#    the model path + flags (do NOT prefix `vllm serve` or it doubles).
#  • Quantization is auto-detected from hf_quant_config.json as `modelopt_mixed` (the
#    checkpoint mixes FP8 + NVFP4), so we do NOT pass --quantization; forcing modelopt_fp4
#    would mis-describe the mixed checkpoint.
#  • seccomp/apparmor are set unconfined: the host's default AppArmor profile denies the
#    `socketpair` syscall uvloop needs at startup (fails with EPERM otherwise on Proxmox).
#  • CUDA_DEVICE_ORDER=PCI_BUS_ID: the two GPUs differ (RTX PRO 4500 / 5000); pin the order.
serve-vllm:
    docker run --rm -it \
        --gpus all --ipc=host --shm-size 16g \
        --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
        -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
        -p {{PORT}}:{{PORT}} \
        -v "{{MODELS_DIR}}:/models" \
        "{{VLLM_IMAGE}}" \
        "/models/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4" \
        --served-model-name "{{SERVED_NAME}}" \
        --tensor-parallel-size {{TP}} \
        --cpu-offload-gb {{CPU_OFFSET}} \
        --host 0.0.0.0 --port {{PORT}} \
        --max-model-len {{CTX}} \
        --gpu-memory-utilization 0.90 \
        --trust-remote-code \
        --enable-auto-tool-choice --tool-call-parser hermes

# Run the server detached (named container) so it survives the SSH session. Logs to docker.
serve-vllm-d:
    docker rm -f nemotron-vllm 2>/dev/null || true
    docker run -d --name nemotron-vllm --restart unless-stopped \
        --gpus all --ipc=host --shm-size 16g \
        --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
        -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
        -p {{PORT}}:{{PORT}} \
        -v "{{MODELS_DIR}}:/models" \
        "{{VLLM_IMAGE}}" \
        "/models/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4" \
        --served-model-name "{{SERVED_NAME}}" \
        --tensor-parallel-size {{TP}} \
        --cpu-offload-gb {{CPU_OFFSET}} \
        --host 0.0.0.0 --port {{PORT}} \
        --max-model-len {{CTX}} \
        --gpu-memory-utilization 0.90 \
        --trust-remote-code \
        --enable-auto-tool-choice --tool-call-parser hermes
    @echo "started container 'nemotron-vllm'; follow with: docker logs -f nemotron-vllm"

# Liveness probe — should list the served model once the server is up.
health:
    curl -fsS "http://localhost:{{PORT}}/v1/models" && echo "" || \
        echo "server not up on :{{PORT}} yet"

# One-shot chat sanity check against the OpenAI-compatible endpoint.
ping:
    curl -fsS "http://localhost:{{PORT}}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer EMPTY" \
        -d '{"model":"{{SERVED_NAME}}","messages":[{"role":"user","content":"reply with OK"}],"temperature":0}'
