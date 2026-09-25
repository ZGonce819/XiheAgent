"""Generate reproducible HumanEval completions with a Xihe checkpoint."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import torch
from transformers import AutoTokenizer

from model_metrics import load_model


def completion_from_text(text: str) -> str:
    text = text.removeprefix("```python\n").removeprefix("```\n")
    text = text.split("```", 1)[0]
    lines = text.splitlines(keepends=True)
    result = []
    for line in lines:
        if result and line.strip() and not line[0].isspace():
            break
        result.append(line)
    return "".join(result)


@torch.inference_mode()
def generate(model, tokenizer, prompt: str, max_new_tokens: int, device: torch.device) -> tuple[str, int]:
    ids = tokenizer(prompt, add_special_tokens=False).input_ids
    if len(ids) + max_new_tokens > model.config.max_position_embeddings:
        raise ValueError("prompt and generation exceed model context length")
    output = model(input_ids=torch.tensor([ids], device=device), use_cache=True, logits_to_keep=1)
    cache = output.past_key_values
    generated = []
    for _ in range(max_new_tokens):
        next_id = int(output.logits[:, -1, :].argmax().item())
        if next_id == tokenizer.eos_token_id:
            break
        generated.append(next_id)
        output = model(
            input_ids=torch.tensor([[next_id]], device=device),
            past_key_values=cache,
            use_cache=True,
            logits_to_keep=1,
        )
        cache = output.past_key_values
    return tokenizer.decode(generated, skip_special_tokens=True), len(generated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-name", default="Xihe-106M")
    parser.add_argument("--role", default="Instruct")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-new-tokens", type=int, default=192)
    parser.add_argument("--prompt-style", choices=["raw", "chat"], default="raw")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(args.project / "model", local_files_only=True)
    model, checkpoint = load_model(args.project, args.model_name, args.role, device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.dataset, "rt", encoding="utf-8") as source, args.output.open("w", encoding="utf-8") as sink:
        for index, line in enumerate(source):
            if index >= args.limit:
                break
            problem = json.loads(line)
            prompt = problem["prompt"]
            if args.prompt_style == "chat":
                prompt = tokenizer.apply_chat_template(
                    [
                        {"role": "system", "content": "Complete the Python function. Output only code inside the function body, with no explanation or Markdown."},
                        {"role": "user", "content": problem["prompt"]},
                    ],
                    tokenize=False,
                    add_generation_prompt=True,
                )
            raw, tokens = generate(model, tokenizer, prompt, args.max_new_tokens, device)
            completion = completion_from_text(raw)
            sample = {
                "task_id": problem["task_id"],
                "completion": completion,
                "raw_generation": raw,
                "generated_tokens": tokens,
                "checkpoint": str(checkpoint),
                "prompt_style": args.prompt_style,
                "decoding": "greedy",
                "max_new_tokens": args.max_new_tokens,
            }
            sink.write(json.dumps(sample, ensure_ascii=False) + "\n")
            sink.flush()
            print(problem["task_id"], repr(completion[:120]), flush=True)


if __name__ == "__main__":
    main()
