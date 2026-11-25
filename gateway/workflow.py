"""Builds the ComfyUI API graph for a Bonsai Image 4B generation.

Bonsai Image 4B is a distilled FLUX.2-klein-4B. FLUX.2 samples with a fixed
FlowMatch schedule (not the FLUX.1 ``KSampler`` + ``simple`` scheduler), so the
graph mirrors the official "Text to Image (FLUX.2 Klein 4B Distilled)" template:

    UnetLoaderGGUF  ──► ┐
    CLIPLoader ─► CLIPTextEncode ─┐
                        ConditioningZeroOut ─┤
                                             ├─► CFGGuider ┐
                        KSamplerSelect ──────┤           │
    Flux2Scheduler ──────────────────────────┤           ├─► SamplerCustomAdvanced
    RandomNoise ─────────────────────────────┘           │         │
    EmptyFlux2LatentImage ───────────────────────────────┘         │
                                                                  ▼
                                            VAELoader ─► VAEDecode ─► SaveImage

Because it is distilled, the default runs at CFG 1.0 for a small number of
steps (4); the negative branch is then zeroed (ConditioningZeroOut) so the
4B text encoder runs only once.

The transformer is loaded from ``flux-2-klein-4b-Q2_K.gguf`` — the same
distilled FLUX.2-klein-4B that Bonsai Image 4B is quantized from, packed as
a 2-bit Q2_K GGUF (149 tensors, identical layout) via the molbal
``UnetLoaderGGUF`` node. The 1-bit (Q1_0) Bonsai file cannot be dequantized
in any current toolchain (no Q1_0 decoder in ggml, the ``gguf`` package, or
molbal), so this is the loadable form of the same weights. If arch
detection rejects the build on a host, the sanctioned fallback is the plain
``UNETLoader`` with the 7.75 GB fp16 ``flux-2-klein-4b.safetensors`` (set
ANOTHERLAB_UNET_LOADER=UNETLoader and point ANOTHERLAB_UNET at it).
"""

from __future__ import annotations

from typing import Any

from .config import Settings

NODE_UNET = "1"
NODE_CLIP = "2"
NODE_VAE = "3"
NODE_POS = "4"
NODE_NEG = "5"
NODE_LATENT = "6"
NODE_DECODE = "8"
NODE_SAVE = "9"
NODE_FLUX2_SCHED = "10"
NODE_NOISE = "11"
NODE_SAMPLER_SELECT = "12"
NODE_CFG_GUIDER = "13"
NODE_SAMPLER_ADV = "14"


def clamp_dimension(value: int, settings: Settings) -> int:
    value = int(value)
    value = max(settings.min_dimension, min(settings.max_dimension, value))
    return value - (value % 16)


def build_workflow(
    settings: Settings,
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> dict[str, Any]:
    width = clamp_dimension(width, settings)
    height = clamp_dimension(height, settings)
    steps = max(1, min(settings.max_steps, int(steps)))
    cfg = max(1.0, min(100.0, float(cfg)))
    seed = int(seed) % (2**64)

    if settings.unet_loader == "UNETLoader":
        unet: dict[str, Any] = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": settings.unet_name, "weight_dtype": "default"},
        }
    else:
        unet = {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": settings.unet_name},
        }

    graph: dict[str, Any] = {
        NODE_UNET: unet,
        NODE_CLIP: {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": settings.clip_name, "type": settings.clip_type},
        },
        NODE_VAE: {
            "class_type": "VAELoader",
            "inputs": {"vae_name": settings.vae_name},
        },
        NODE_POS: {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": [NODE_CLIP, 0]},
        },
        NODE_LATENT: {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        NODE_FLUX2_SCHED: {
            "class_type": "Flux2Scheduler",
            "inputs": {"steps": steps, "width": width, "height": height},
        },
        NODE_NOISE: {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": seed},
        },
        NODE_SAMPLER_SELECT: {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": settings.sampler},
        },
        NODE_CFG_GUIDER: {
            "class_type": "CFGGuider",
            "inputs": {
                "model": [NODE_UNET, 0],
                "positive": [NODE_POS, 0],
                "negative": [NODE_NEG, 0],
                "cfg": cfg,
            },
        },
        NODE_SAMPLER_ADV: {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": [NODE_NOISE, 0],
                "guider": [NODE_CFG_GUIDER, 0],
                "sampler": [NODE_SAMPLER_SELECT, 0],
                "sigmas": [NODE_FLUX2_SCHED, 0],
                "latent_image": [NODE_LATENT, 0],
            },
        },
        NODE_DECODE: {
            "class_type": "VAEDecode",
            "inputs": {"samples": [NODE_SAMPLER_ADV, 0], "vae": [NODE_VAE, 0]},
        },
        NODE_SAVE: {
            "class_type": "SaveImage",
            "inputs": {"images": [NODE_DECODE, 0], "filename_prefix": "anotherlab"},
        },
    }

    # CFG 1 (Bonsai's native distilled setting) means the negative branch is
    # never evaluated. Zeroing it avoids a second pass through the 4B text
    # encoder, which is expensive on CPU.
    if cfg <= 1.01:
        graph[NODE_NEG] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": [NODE_POS, 0]},
        }
    else:
        graph[NODE_NEG] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": [NODE_CLIP, 0]},
        }

    return graph


def estimated_seconds(settings: Settings, width: int, height: int, steps: int) -> int:
    """Rough ETA used by the UI before the first job of a size completes.

    Calibrate on the box with `make trial` and scale via ANOTHERLAB_ETA_SCALE.
    Scales with pixels and steps.
    """
    megapixels = (width * height) / (512 * 512)
    base = 120 + 480 * megapixels * (steps / 4.0)
    return max(15, int(base * max(0.001, settings.eta_scale)))
