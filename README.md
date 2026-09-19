# Qwen Image Edit 2511 Q5_K_M + LanPaint for RunPod Serverless


## Проверки и ограничения

Статически проверяются синтаксис Python, структура workflow, наличие всех патчируемых node ID и отсутствие секретов. Docker image и GPU inference на этой машине не запускались. Качество границы маски, реальный расход VRAM и холодный запуск необходимо проверить одним заданием на RunPod.

Upstream worker: https://github.com/WaromiV/qwen-image-edit-2511

LanPaint: https://github.com/scraed/LanPaint

ComfyUI-GGUF: https://github.com/city96/ComfyUI-GGUF
