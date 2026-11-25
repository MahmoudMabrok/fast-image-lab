"""The ComfyUI graph must stay valid and cheap on CPU."""

from __future__ import annotations

from gateway.config import Settings
from gateway.workflow import build_workflow, clamp_dimension, estimated_seconds


def make_settings(**overrides) -> Settings:
    base = Settings()
    if not overrides:
        return base
    import dataclasses

    return dataclasses.replace(base, **overrides)


def test_cfg_one_skips_the_negative_text_encoder():
    graph = build_workflow(
        make_settings(),
        prompt="a cat",
        negative="blurry",
        width=512,
        height=512,
        steps=4,
        cfg=1.0,
        seed=1,
    )
    assert graph["5"]["class_type"] == "ConditioningZeroOut"
    assert graph["5"]["inputs"]["conditioning"] == ["4", 0]
    # Only one CLIPTextEncode node exists, so the 4B encoder runs once.
    encoders = [n for n in graph.values() if n["class_type"] == "CLIPTextEncode"]
    assert len(encoders) == 1


def test_cfg_above_one_encodes_the_negative_prompt():
    graph = build_workflow(
        make_settings(),
        prompt="a cat",
        negative="blurry",
        width=512,
        height=512,
        steps=4,
        cfg=3.5,
        seed=1,
    )
    assert graph["5"]["class_type"] == "CLIPTextEncode"
    assert graph["5"]["inputs"]["text"] == "blurry"
    assert graph["13"]["inputs"]["cfg"] == 3.5


def test_dimensions_are_clamped_to_multiples_of_sixteen():
    settings = make_settings(min_dimension=256, max_dimension=1024)
    assert clamp_dimension(500, settings) == 496
    assert clamp_dimension(10, settings) == 256
    assert clamp_dimension(4096, settings) == 1024


def test_workflow_uses_the_gguf_loader_and_flux2_custom_sampler():
    graph = build_workflow(
        make_settings(unet_loader="UnetLoaderGGUF"),
        prompt="a cat",
        negative="",
        width=512,
        height=512,
        steps=4,
        cfg=1.0,
        seed=1,
    )
    assert graph["1"]["class_type"] == "UnetLoaderGGUF"
    assert graph["1"]["inputs"]["unet_name"] == "flux-2-klein-4b-Q2_K.gguf"
    assert graph["2"]["class_type"] == "CLIPLoader"
    assert graph["2"]["inputs"]["type"] == "flux2"
    # No LoRA anywhere: the model is distilled.
    loras = [n for n in graph.values() if "Lora" in n["class_type"]]
    assert loras == []
    assert graph["6"]["class_type"] == "EmptyFlux2LatentImage"
    # FLUX.2 sampling: FlowMatch schedule + custom-sampler pipeline
    # (mirrors the official "FLUX.2 Klein 4B Distilled" template).
    assert graph["10"]["class_type"] == "Flux2Scheduler"
    assert graph["10"]["inputs"] == {"steps": 4, "width": 512, "height": 512}
    assert graph["11"]["class_type"] == "RandomNoise"
    assert graph["11"]["inputs"]["noise_seed"] == 1
    assert graph["12"]["class_type"] == "KSamplerSelect"
    assert graph["12"]["inputs"]["sampler_name"] == "euler"
    assert graph["13"]["class_type"] == "CFGGuider"
    assert graph["13"]["inputs"]["cfg"] == 1.0
    assert graph["13"]["inputs"]["negative"] == ["5", 0]
    assert graph["14"]["class_type"] == "SamplerCustomAdvanced"
    assert graph["14"]["inputs"]["sigmas"] == ["10", 0]
    assert graph["14"]["inputs"]["noise"] == ["11", 0]
    assert graph["14"]["inputs"]["guider"] == ["13", 0]
    assert graph["14"]["inputs"]["sampler"] == ["12", 0]
    assert graph["14"]["inputs"]["latent_image"] == ["6", 0]
    assert graph["8"]["class_type"] == "VAEDecode"
    assert graph["8"]["inputs"]["samples"] == ["14", 0]
    assert graph["9"]["inputs"]["filename_prefix"] == "anotherlab"
    # No FLUX.1-style KSampler in a FLUX.2 graph.
    assert [n["class_type"] for n in graph.values()].count("KSampler") == 0


def test_safetensors_fallback_uses_the_plain_loader():
    graph = build_workflow(
        make_settings(unet_loader="UNETLoader", unet_name="flux-2-klein-4b.safetensors"),
        prompt="a cat",
        negative="",
        width=512,
        height=512,
        steps=4,
        cfg=1.0,
        seed=1,
    )
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["1"]["inputs"]["unet_name"] == "flux-2-klein-4b.safetensors"


def test_steps_are_capped_at_the_max():
    settings = make_settings(max_steps=20)
    graph = build_workflow(
        settings, prompt="a cat", negative="", width=512, height=512,
        steps=99, cfg=1.0, seed=1,
    )
    assert graph["10"]["inputs"]["steps"] == 20


def test_eta_grows_with_pixels_and_steps():
    settings = make_settings(eta_scale=1.0)
    small = estimated_seconds(settings, 512, 512, 4)
    big = estimated_seconds(settings, 1024, 1024, 4)
    longer = estimated_seconds(settings, 512, 512, 20)
    assert 100 <= small <= 640
    assert big > small
    assert longer > small
    fast = estimated_seconds(make_settings(eta_scale=0.04), 1024, 1024, 4)
    assert fast < small
