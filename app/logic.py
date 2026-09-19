import base64
import copy
import io
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests
from PIL import Image

COMFYUI_DIR = Path(os.getenv("COMFYUI_DIR", "/comfyui"))
COMFY_HOST = os.getenv("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.getenv("COMFY_PORT", "8188"))
COMFY_BASE_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
WORKFLOW_PATH = Path(
    os.getenv(
        "WORKFLOW_PATH",
        "/app/workflow/qwen_2511_q5_lanpaint_inpaint_api.json",
    )
)
MODEL_ROOT = Path(os.getenv("MODEL_ROOT", "/runpod-volume/models/qwen-2511-lanpaint"))

GGUF_FILENAME = "qwen-image-edit-2511-Q5_K_M.gguf"
TEXT_ENCODER_FILENAME = "Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf"
MMPROJ_FILENAME = "Qwen2.5-VL-7B-Instruct-mmproj-BF16.gguf"
VAE_FILENAME = "qwen_image_vae.safetensors"

MODEL_SPECS = [
    {
        "filename": GGUF_FILENAME,
        "url": f"https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/{GGUF_FILENAME}",
        "subdir": "unet",
    },
    {
        "filename": TEXT_ENCODER_FILENAME,
        "url": f"https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/{TEXT_ENCODER_FILENAME}",
        "subdir": "text_encoders",
    },
    {
        "filename": MMPROJ_FILENAME,
        "url": "https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/mmproj-BF16.gguf",
        "subdir": "text_encoders",
    },
    {
        "filename": VAE_FILENAME,
        "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors",
        "subdir": "vae",
    },
]

START_LOCK = threading.Lock()
SERVER_PROCESS = None


def log_event(stage: str, **fields) -> None:
    print(json.dumps({"stage": stage, **fields}, sort_keys=True, default=str), flush=True)


def _download_headers() -> dict:
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    headers = {"User-Agent": "qwen-2511-lanpaint-runpod/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = _download_headers()
    if offset:
        headers["Range"] = f"bytes={offset}-"
    log_event("model.download.start", filename=destination.name, resume_bytes=offset)
    with requests.get(url, stream=True, timeout=(30, 600), headers=headers) as response:
        if offset and response.status_code != 206:
            offset = 0
        response.raise_for_status()
        mode = "ab" if offset and response.status_code == 206 else "wb"
        with partial.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    handle.write(chunk)
    partial.replace(destination)
    log_event("model.download.done", filename=destination.name, bytes=destination.stat().st_size)


def _link(source: Path, subdir: str) -> None:
    target = COMFYUI_DIR / "models" / subdir / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        return
    target.symlink_to(source)


def ensure_model_files() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    for spec in MODEL_SPECS:
        source = MODEL_ROOT / spec["filename"]
        if not source.exists():
            _download_file(spec["url"], source)
        _link(source, spec["subdir"])


def _wait_until_ready(timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{COMFY_BASE_URL}/system_stats", timeout=5)
            if response.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    log_path = Path("/tmp/comfyui-qwen.log")
    tail = log_path.read_text(errors="ignore")[-5000:] if log_path.exists() else ""
    raise RuntimeError(f"ComfyUI did not become ready. Last log output:\n{tail}")


def warmup_model(*, request_id=None) -> None:
    global SERVER_PROCESS
    with START_LOCK:
        if SERVER_PROCESS is not None and SERVER_PROCESS.poll() is None:
            return
        log_event("warmup.start", request_id=request_id)
        ensure_model_files()
        log_path = Path("/tmp/comfyui-qwen.log")
        with log_path.open("ab") as log_file:
            SERVER_PROCESS = subprocess.Popen(
                ["/app/start.sh"],
                cwd=COMFYUI_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
            )
        _wait_until_ready()
        log_event("warmup.done", request_id=request_id)


def _decode_image(value: str, mode: str) -> Image.Image:
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        raw = base64.b64decode(value, validate=True)
        return Image.open(io.BytesIO(raw)).convert(mode)
    except Exception as exc:
        raise ValueError("Input must be a valid base64-encoded PNG or JPEG") from exc


def _write_inputs(image: Image.Image, mask: Image.Image, request_id: str) -> tuple[str, str]:
    if image.size != mask.size:
        raise ValueError(f"Mask size {mask.size} must match image size {image.size}")
    input_dir = COMFYUI_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    image_name = f"{request_id}_input.png"
    mask_name = f"{request_id}_mask.png"
    image.save(input_dir / image_name, format="PNG")
    mask.save(input_dir / mask_name, format="PNG")
    return image_name, mask_name


def _patch_workflow(
    image_name: str,
    mask_name: str,
    prompt: str,
    seed: int,
    steps: int,
    cfg: float,
    lanpaint_steps: int,
    filename_prefix: str,
) -> dict:
    workflow = copy.deepcopy(json.loads(WORKFLOW_PATH.read_text(encoding="utf-8")))
    workflow["78"]["inputs"]["image"] = image_name
    workflow["130"]["inputs"]["image"] = mask_name
    workflow["37"]["inputs"]["unet_name"] = GGUF_FILENAME
    workflow["38"]["inputs"]["clip_name"] = TEXT_ENCODER_FILENAME
    workflow["39"]["inputs"]["vae_name"] = VAE_FILENAME
    workflow["111"]["inputs"]["prompt"] = prompt
    workflow["110"]["inputs"]["prompt"] = ""
    workflow["125"]["inputs"]["seed"] = seed
    workflow["125"]["inputs"]["steps"] = steps
    workflow["125"]["inputs"]["cfg"] = cfg
    workflow["125"]["inputs"]["LanPaint_NumSteps"] = lanpaint_steps
    workflow["127"]["inputs"]["filename_prefix"] = filename_prefix
    return workflow


def _run_workflow(workflow: dict, timeout: int = 1800) -> tuple[bytes, str]:
    response = requests.post(
        f"{COMFY_BASE_URL}/prompt",
        json={"prompt": workflow, "client_id": str(uuid.uuid4())},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("node_errors"):
        raise RuntimeError(f"ComfyUI rejected the workflow: {payload['node_errors']}")
    prompt_id = payload["prompt_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history_response = requests.get(
            f"{COMFY_BASE_URL}/history/{prompt_id}", timeout=30
        )
        history_response.raise_for_status()
        history = history_response.json().get(prompt_id)
        if history:
            images = (history.get("outputs", {}).get("127", {}).get("images") or [])
            if not images:
                raise RuntimeError(f"ComfyUI produced no image: {history.get('status')}")
            meta = images[0]
            query = urlencode(
                {
                    "filename": meta["filename"],
                    "subfolder": meta.get("subfolder", ""),
                    "type": meta.get("type", "output"),
                }
            )
            image_response = requests.get(f"{COMFY_BASE_URL}/view?{query}", timeout=120)
            image_response.raise_for_status()
            return image_response.content, prompt_id
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id}")


def edit_image(job_input: dict, *, request_id=None) -> dict:
    prompt = str(job_input.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Missing required field: prompt")
    if not job_input.get("image_base64"):
        raise ValueError("Missing required field: image_base64")
    if not job_input.get("mask_base64"):
        raise ValueError("Missing required field: mask_base64")

    image = _decode_image(job_input["image_base64"], "RGB")
    mask = _decode_image(job_input["mask_base64"], "L")
    request_id = request_id or uuid.uuid4().hex
    seed = int(job_input.get("seed", 0))
    steps = int(job_input.get("steps", 20))
    cfg = float(job_input.get("cfg", 2.5))
    lanpaint_steps = int(job_input.get("lanpaint_steps", 1))
    if not 1 <= steps <= 100:
        raise ValueError("steps must be between 1 and 100")
    if not 0 <= lanpaint_steps <= 100:
        raise ValueError("lanpaint_steps must be between 0 and 100")

    warmup_model()
    image_name, mask_name = _write_inputs(image, mask, request_id)
    workflow = _patch_workflow(
        image_name,
        mask_name,
        prompt,
        seed,
        steps,
        cfg,
        lanpaint_steps,
        f"Qwen2511_LanPaint_{request_id}",
    )
    output_bytes, prompt_id = _run_workflow(workflow)
    output = Image.open(io.BytesIO(output_bytes))
    return {
        "ok": True,
        "model_id": f"unsloth/Qwen-Image-Edit-2511-GGUF::{GGUF_FILENAME}",
        "runtime": "comfyui-gguf-lanpaint",
        "seed": seed,
        "steps": steps,
        "cfg": cfg,
        "lanpaint_steps": lanpaint_steps,
        "width": output.width,
        "height": output.height,
        "mime_type": "image/png",
        "image_base64": base64.b64encode(output_bytes).decode("ascii"),
        "prompt_id": prompt_id,
    }
