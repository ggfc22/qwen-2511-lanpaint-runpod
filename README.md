# Qwen Image Edit 2511 Q5_K_M + LanPaint for RunPod Serverless

Собственный Serverless worker, который обходит сломанный RunPod ComfyUI Workflow Wizard.

Основан на готовом worker `WaromiV/qwen-image-edit-2511`, а inpaint-граф — на официальном примере LanPaint. Изменения ограничены выбранным Q5_K_M, отдельной маской и необходимыми зависимостями.

## Что внутри

- публичная основа `runpod/worker-comfyui:5.10.0-base-cuda12.8.1`;
- `city96/ComfyUI-GGUF`;
- `scraed/LanPaint`;
- API workflow `image + mask + prompt`;
- RunPod queue handler;
- автоматическая докачка моделей с продолжением оборванной загрузки;
- GitHub Actions для публикации конечного образа в GHCR.

Веса не входят в Docker image. При первом настоящем запросе worker загружает их в подключённый RunPod Network Volume и затем использует повторно.

## Модели

- `qwen-image-edit-2511-Q5_K_M.gguf`;
- `Qwen2.5-VL-7B-Instruct-UD-Q4_K_XL.gguf`;
- `Qwen2.5-VL-7B-Instruct-mmproj-BF16.gguf`;
- `qwen_image_vae.safetensors`.

Все эти файлы публичные; `HF_TOKEN` не требуется. Не добавляйте токены в репозиторий или Dockerfile.

## Публикация в GHCR

1. Создайте на GitHub пустой репозиторий `qwen-2511-lanpaint-runpod`.
2. Поместите в корень репозитория всё содержимое этой папки, включая `.github`.
3. После первого push откройте вкладку **Actions**. Workflow `Build and publish RunPod worker` соберёт образ для Linux/AMD64.
4. После успешной сборки откройте профиль GitHub → **Packages** → `qwen-2511-lanpaint-runpod` → **Package settings** → **Change visibility** → `Public`.
5. Конечный образ будет называться:

   `ghcr.io/ВАШ_GITHUB_LOGIN/qwen-2511-lanpaint-runpod:latest`

GitHub Actions использует штатный `GITHUB_TOKEN`; создавать и передавать отдельный registry token для сборки не нужно.

## Настройка RunPod

Перед template создайте **Network Volume минимум 50 GB** в том же регионе, где будет endpoint. Он обязателен для сохранения примерно 20+ GB моделей между холодными запусками.

Создайте Serverless Template:

- Container image: конечный GHCR image выше;
- Container disk: 20 GB;
- Network Volume: созданный том;
- Volume mount: `/runpod-volume`;
- Environment: `PRELOAD_MODEL=0`;
- никакие registry credentials не нужны после перевода GHCR package в Public.

Создайте Queue endpoint:

- `workersMin: 0`;
- `workersMax: 1`;
- сначала GPU с 24 GB VRAM;
- execution timeout установите не меньше 3600 секунд для первого запроса с загрузкой моделей.

24 GB — кандидат для проверки, а не подтверждённая гарантия. Q5_K_M весит около 15 GB, дополнительно нужны энкодер, VAE и рабочая память. Если реальный запуск даст CUDA OOM, потребуется GPU с большей VRAM либо отдельное решение пользователя перейти на более лёгкий квант.

## Запрос

Маска должна иметь тот же размер, что и изображение: белое меняется, чёрное сохраняется.

```json
{
  "input": {
    "prompt": "Replace the masked area with matching fabric.",
    "image_base64": "<base64 PNG>",
    "mask_base64": "<base64 grayscale PNG>",
    "seed": 0,
    "steps": 20,
    "cfg": 2.5,
    "lanpaint_steps": 1
  }
}
```

Результат возвращается как `output.image_base64`.

## Первый запуск

Первый запрос будет намного дольше следующих: worker загрузит четыре файла моделей в Network Volume. Загрузка пишет `.part` и продолжает её после обрыва. Не отправляйте повторные задания вслепую, пока первое ещё выполняется.

Начните с подготовленного локально crop около 1024×1024. SAM3, BiRef, Ollama, координаты crop и обратная вставка в полный исходник остаются локально.

## Проверки и ограничения

Статически проверяются синтаксис Python, структура workflow, наличие всех патчируемых node ID и отсутствие секретов. Docker image и GPU inference на этой машине не запускались. Качество границы маски, реальный расход VRAM и холодный запуск необходимо проверить одним заданием на RunPod.

Upstream worker: https://github.com/WaromiV/qwen-image-edit-2511

LanPaint: https://github.com/scraed/LanPaint

ComfyUI-GGUF: https://github.com/city96/ComfyUI-GGUF
