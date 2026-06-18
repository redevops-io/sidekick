# Serve the local self-hosted model that powers sidekick's `selfhosted` backend.
#
# Target hardware: the local **evo-x2 (AMD Ryzen AI Max "Strix Halo")** box with ~96 GB
# allocated to the iGPU as VRAM (unified memory). Model:
#   mradermacher/Qwen3.5-122B-A10B-GGUF   (q4 / Q4_K_M)   — a ~122B-param MoE, ~10B active.
#
# Whichever recipe you run, it exposes an **OpenAI-compatible `/v1` API on :8000**, which is
# exactly what sidekick's selfhosted backend talks to (VLLM_BASE_URL=http://localhost:8000/v1).
#
#   just -f scripts/serve_vllm.justfile <recipe>
#
# ── Which recipe? ────────────────────────────────────────────────────────────────────────
#  • serve-llamacpp  → RECOMMENDED on Strix Halo. llama.cpp serves GGUF natively on ROCm/
#                      Vulkan and is the de-facto runtime for this hardware.
#  • serve-vllm      → as literally requested (vLLM + --quantization gguf). vLLM's GGUF path
#                      is EXPERIMENTAL and CUDA-first; GGUF + MoE on ROCm may be unsupported
#                      or slow. Kept here so the requested path is documented and runnable;
#                      fall back to serve-llamacpp if vLLM rejects the GGUF.
# ─────────────────────────────────────────────────────────────────────────────────────────

HF_REPO       := "mradermacher/Qwen3.5-122B-A10B-GGUF"
# q4 quant file inside the repo. Large MoE GGUFs are often split into NNNNN-of-MMMMM parts;
# point GGUF_FILE at the first shard — both runtimes auto-discover the rest.
GGUF_FILE     := "Qwen3.5-122B-A10B.Q4_K_M.gguf"
SERVED_NAME   := "Qwen3.5-122B-A10B"
MODELS_DIR    := env_var_or_default("MODELS_DIR", "/mnt/models")
PORT          := "8000"
# Context window. The 96 GB split leaves generous room beyond the q4 weights (~70 GB) for KV
# cache; 32k is a safe default for merge-conflict-resolution prompts. Raise if your box allows.
CTX           := "32768"

# Download the q4 GGUF from Hugging Face into MODELS_DIR.
fetch:
    mkdir -p "{{MODELS_DIR}}"
    uv tool run --from huggingface_hub huggingface-cli download \
        "{{HF_REPO}}" "{{GGUF_FILE}}" \
        --local-dir "{{MODELS_DIR}}/Qwen3.5-122B-A10B-GGUF"
    @echo "Fetched to {{MODELS_DIR}}/Qwen3.5-122B-A10B-GGUF/{{GGUF_FILE}}"

# RECOMMENDED: llama.cpp server (ROCm/Vulkan) — native GGUF, OpenAI-compatible /v1.
# Uses the prebuilt ROCm image; on Strix Halo (gfx1151) the Vulkan build also works well.
serve-llamacpp:
    docker run --rm -it \
        --device /dev/kfd --device /dev/dri \
        --group-add video --ipc=host \
        -p {{PORT}}:{{PORT}} \
        -v "{{MODELS_DIR}}:/models" \
        ghcr.io/ggml-org/llama.cpp:server-rocm \
        -m "/models/Qwen3.5-122B-A10B-GGUF/{{GGUF_FILE}}" \
        --alias "{{SERVED_NAME}}" \
        --host 0.0.0.0 --port {{PORT}} \
        -c {{CTX}} \
        -ngl 999 \
        --jinja \
        --parallel 2

# AS-REQUESTED: vLLM with GGUF (ROCm image). EXPERIMENTAL — see header note. If vLLM refuses
# the GGUF (common for MoE GGUFs), use serve-llamacpp instead; sidekick needs no change since
# both speak OpenAI /v1.
serve-vllm:
    docker run --rm -it \
        --device /dev/kfd --device /dev/dri \
        --group-add video --ipc=host --shm-size 16g \
        -p {{PORT}}:{{PORT}} \
        -v "{{MODELS_DIR}}:/models" \
        rocm/vllm:latest \
        vllm serve "/models/Qwen3.5-122B-A10B-GGUF/{{GGUF_FILE}}" \
        --quantization gguf \
        --served-model-name "{{SERVED_NAME}}" \
        --host 0.0.0.0 --port {{PORT}} \
        --max-model-len {{CTX}} \
        --gpu-memory-utilization 0.92 \
        --enable-auto-tool-choice --tool-call-parser hermes

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
