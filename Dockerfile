FROM runpod/worker-comfyui:5.10.0-base-cuda12.8.1

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NVIDIA_DISABLE_REQUIRE=1 \
    COMFYUI_DIR=/comfyui \
    COMFY_HOST=127.0.0.1 \
    COMFY_PORT=8188 \
    MODEL_ROOT=/runpod-volume/models/qwen-2511-lanpaint \
    WORKFLOW_PATH=/app/workflow/qwen_2511_q5_lanpaint_inpaint_api.json \
    PRELOAD_MODEL=0

WORKDIR /app
COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/requirements.txt && \
    rm -rf /comfyui/custom_nodes/ComfyUI-GGUF /comfyui/custom_nodes/LanPaint && \
    git clone --depth=1 https://github.com/city96/ComfyUI-GGUF /comfyui/custom_nodes/ComfyUI-GGUF && \
    pip install -r /comfyui/custom_nodes/ComfyUI-GGUF/requirements.txt && \
    git clone --depth=1 https://github.com/scraed/LanPaint /comfyui/custom_nodes/LanPaint && \
    if [ -f /comfyui/custom_nodes/LanPaint/requirements.txt ]; then \
      pip install -r /comfyui/custom_nodes/LanPaint/requirements.txt; \
    fi

COPY handler.py /app/handler.py
COPY app /app/app
COPY workflow /app/workflow
COPY start.sh /app/start.sh
COPY test_input.json /app/test_input.json
RUN chmod +x /app/start.sh
CMD ["python3", "-u", "handler.py"]
