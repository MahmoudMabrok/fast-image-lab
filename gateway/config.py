"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Engine
    comfy_url: str = field(default_factory=lambda: os.getenv("COMFY_URL", "http://127.0.0.1:8188"))
    comfy_output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("COMFY_OUTPUT_DIR", "/srv/ComfyUI/output"))
    )
    unet_name: str = field(
        default_factory=lambda: os.getenv("ANOTHERLAB_UNET", "flux-2-klein-4b-Q2_K.gguf")
    )
    unet_loader: str = field(
        default_factory=lambda: os.getenv("ANOTHERLAB_UNET_LOADER", "UnetLoaderGGUF")
    )
    clip_name: str = field(
        default_factory=lambda: os.getenv("ANOTHERLAB_CLIP", "qwen_3_4b_fp4_flux2.safetensors")
    )
    clip_type: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_CLIP_TYPE", "flux2"))
    vae_name: str = field(
        default_factory=lambda: os.getenv("ANOTHERLAB_VAE", "flux2-vae.safetensors")
    )
    sampler: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_SAMPLER", "euler"))
    device_label: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_DEVICE_LABEL", "CPU"))
    eta_scale: float = field(default_factory=lambda: _float("ANOTHERLAB_ETA_SCALE", 1.0))
    model_name: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_MODEL_NAME", "Bonsai Image 4B"))
    model_base: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_MODEL_BASE", "FLUX.2-klein-4B"))

    # Defaults and limits
    default_width: int = field(default_factory=lambda: _int("ANOTHERLAB_DEFAULT_WIDTH", 512))
    default_height: int = field(default_factory=lambda: _int("ANOTHERLAB_DEFAULT_HEIGHT", 512))
    default_steps: int = field(default_factory=lambda: _int("ANOTHERLAB_DEFAULT_STEPS", 4))
    default_cfg: float = field(default_factory=lambda: _float("ANOTHERLAB_DEFAULT_CFG", 1.0))
    default_negative: str = field(
        default_factory=lambda: os.getenv(
            "ANOTHERLAB_DEFAULT_NEGATIVE",
            "nsfw, worst quality, blurry, jpeg artifact",
        )
    )
    max_steps: int = field(default_factory=lambda: _int("ANOTHERLAB_MAX_STEPS", 20))
    max_dimension: int = field(default_factory=lambda: _int("ANOTHERLAB_MAX_DIMENSION", 1024))
    min_dimension: int = field(default_factory=lambda: _int("ANOTHERLAB_MIN_DIMENSION", 256))
    max_prompt_chars: int = field(default_factory=lambda: _int("ANOTHERLAB_MAX_PROMPT_CHARS", 4000))

    # Jobs
    ttl_seconds: int = field(default_factory=lambda: _int("ANOTHERLAB_TTL_SECONDS", 18000))
    job_timeout_seconds: int = field(default_factory=lambda: _int("ANOTHERLAB_JOB_TIMEOUT", 3600))
    history_keep_seconds: int = field(
        default_factory=lambda: _int("ANOTHERLAB_HISTORY_KEEP_SECONDS", 86400)
    )
    mcp_wait_default: int = field(default_factory=lambda: _int("ANOTHERLAB_MCP_WAIT", 90))
    mcp_wait_max: int = field(default_factory=lambda: _int("ANOTHERLAB_MCP_WAIT_MAX", 600))

    # Paths / server
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("ANOTHERLAB_DATA_DIR", "/srv/anotherlab/data")))
    host: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("ANOTHERLAB_PORT", 8080))
    api_key: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_API_KEY", ""))
    public_url: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_PUBLIC_URL", ""))
    cors_origins: str = field(default_factory=lambda: os.getenv("ANOTHERLAB_CORS_ORIGINS", "*"))

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "anotherlab.sqlite3"

    def ensure_dirs(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def public_base(self) -> str:
        return self.public_url.rstrip("/") if self.public_url else ""

settings = Settings()
