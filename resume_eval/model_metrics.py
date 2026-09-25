"""Measure text perplexity and cached greedy decoding for Xihe checkpoints."""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoTokenizer


def synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def load_model(project: Path, model_name: str, role: str, device: torch.device):
    sys.path.insert(0, str(project))
    from model import XiheConfig, XiheForCausalLM

    config = XiheConfig.from_json_file(str(project / "out" / f"{model_name}-config.json"))
    model = XiheForCausalLM(config)
    checkpoint = project / "out" / f"{model_name}-{role}.pth"
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.to(device).eval().requires_grad_(False)
    return model, checkpoint


@torch.inference_mode()
def measure_ppl(model, token_ids: list[int], device: torch.device, window: int) -> dict:
    total_nll = 0.0
    scored_tokens = 0
    for start in range(0, len(token_ids) - 1, window):
        ids = token_ids[start : start + window + 1]
        if len(ids) < 2:
            break
        x = torch.tensor([ids[:-1]], dtype=torch.long, device=device)
        y = torch.tensor(ids[1:], dtype=torch.long, device=device)
        logits = model(input_ids=x, use_cache=False).logits
        total_nll += F.cross_entropy(logits.reshape(-1, logits.size(-1)), y.reshape(-1), reduction="sum").item()
        scored_tokens += y.numel()
    mean_nll = total_nll / scored_tokens
    return {"scored_tokens": scored_tokens, "mean_nll": mean_nll, "ppl": math.exp(mean_nll)}


@torch.inference_mode()
def decode_once(model, prompt: list[int], new_tokens: int, device: torch.device) -> dict:
    input_ids = torch.tensor([prompt], dtype=torch.long, device=device)
    synchronize(device)
    started = time.perf_counter()
    output = model(input_ids=input_ids, use_cache=True, logits_to_keep=1)
    synchronize(device)
    first_token_at = time.perf_counter()
    next_id = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)
    cache = output.past_key_values
    for _ in range(new_tokens - 1):
        output = model(input_ids=next_id, past_key_values=cache, use_cache=True, logits_to_keep=1)
        next_id = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)
        cache = output.past_key_values
    synchronize(device)
    ended = time.perf_counter()
    return {
        "ttft_seconds": first_token_at - started,
        "decode_seconds": ended - first_token_at,
        "decode_tokens": new_tokens - 1,
        "decode_tokens_per_second": (new_tokens - 1) / (ended - first_token_at),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--model-name", default="Xihe-106M")
    parser.add_argument("--roles", nargs="+", default=["Base", "Instruct", "Distill"])
    parser.add_argument("--text", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--window", type=int, default=256)
    parser.add_argument("--prompt-tokens", type=int, default=128)
    parser.add_argument("--new-tokens", type=int, default=32)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.window < 2 or args.max_tokens < 2 or args.prompt_tokens < 1 or args.new_tokens < 2:
        parser.error("window/max-tokens/prompt-tokens/new-tokens must be positive usable lengths")
    device_name = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if device_name == "auto":
        device_name = "cpu"
    if device_name == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA is unavailable in this PyTorch installation")
    device = torch.device(device_name)
    tokenizer = AutoTokenizer.from_pretrained(args.project / "model", local_files_only=True)
    text = args.text.read_text(encoding="utf-8")
    token_ids = tokenizer(text, add_special_tokens=False).input_ids[: args.max_tokens]
    if len(token_ids) < args.prompt_tokens:
        parser.error("source text contains fewer tokens than --prompt-tokens")

    result = {
        "source_text": str(args.text),
        "source_note": "Post-training project documentation; check overlap before claiming held-out PPL.",
        "model_name": args.model_name,
        "model_tokenizer": str(args.project / "model"),
        "text_tokens": len(token_ids),
        "window": args.window,
        "prompt_tokens": args.prompt_tokens,
        "new_tokens": args.new_tokens,
        "warmups": args.warmups,
        "repeats": args.repeats,
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "torch": torch.__version__,
        "python": platform.python_version(),
        "roles": {},
    }
    for role in args.roles:
        model, checkpoint = load_model(args.project, args.model_name, role, device)
        ppl = measure_ppl(model, token_ids, device, args.window)
        prompt = token_ids[: args.prompt_tokens]
        for _ in range(args.warmups):
            decode_once(model, prompt, args.new_tokens, device)
        runs = [decode_once(model, prompt, args.new_tokens, device) for _ in range(args.repeats)]
        result["roles"][role] = {
            "checkpoint": str(checkpoint),
            "ppl": ppl,
            "timing": {
                "ttft_seconds_median": statistics.median(r["ttft_seconds"] for r in runs),
                "decode_tokens_per_second_median": statistics.median(r["decode_tokens_per_second"] for r in runs),
                "runs": runs,
            },
        }
        print(role, json.dumps(result["roles"][role], ensure_ascii=False), flush=True)
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
