"""Read image pixels through the vision model and audit the transcription."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from PIL import Image

from agent.middleware.final_response import FinalResponseMiddleware
from utils.message_utils import message_content_to_text


def _json_response(reply):
    raw = message_content_to_text(reply.content).strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.I)
    raw = re.sub(r'\s*```$', '', raw)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Image reader must return a JSON object')
    return value


def _image_views(raw, task):
    """Enlarge small originals and optionally separate red/green evidence.

    Mask pixels deterministically, without OCR, answer inference or answer leakage.
    The original and every derived view are supplied to both reader and auditor.
    """
    with Image.open(io.BytesIO(raw)) as opened:
        original = opened.convert("RGB")
    scale = min(4.0, max(1.0, 1600 / max(original.size)))
    size = tuple(max(1, round(d * scale)) for d in original.size)
    views = [("Original image (enlarged without changing colours)", original)]
    if re.search(r"red|红", task, re.I) and re.search(r"green|绿", task, re.I):
        for colour in ("red", "green"):
            mask = Image.new("RGB", original.size, "white")
            pixels = []
            for r, g, b in (original.get_flattened_data() if hasattr(original, "get_flattened_data") else original.getdata()):
                selected = (r > 60 and r > 1.3 * g and r > 1.3 * b) if colour == "red" else (g > 60 and g > 1.1 * r and g > 1.1 * b)
                pixels.append((0, 0, 0) if selected else (255, 255, 255))
            mask.putdata(pixels)
            views.append((f"{colour} dominant-pixel mask: black marks are {colour} pixels from the original; white is omitted", mask))
    blocks = []
    for label, picture in views:
        buffer = io.BytesIO()
        picture.resize(size, Image.Resampling.NEAREST).save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode()
        blocks.extend([{"type": "text", "text": label},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"}}])
    return blocks


def create_inspect_image_tool(backend, model):
    @tool
    def inspect_image(file_path: str, task: str) -> str:
        """Inspect actual image pixels and audit their transcription.

        Use for image text, tables, numbers, colours and objects BEFORE calculating
        from an image. Specify regions, reading order and colour labels in task.
        Returns grounded observations and a pixel hash, never the final answer.
        If uncertain, crop/enlarge the source and inspect again; do not guess.
        """
        media = mimetypes.guess_type(file_path)[0] or ''
        if media not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
            raise ValueError('inspect_image requires PNG, JPEG, WebP or GIF')
        result = backend.read(file_path)
        if result.error or not result.file_data:
            raise ValueError(result.error or 'Image could not be read')
        if result.file_data.get('encoding') != 'base64':
            raise ValueError('Backend did not return image bytes as base64')
        encoded = result.file_data['content']
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError('Image exceeds 12 MiB; resize or crop and retry')
        image_blocks = _image_views(raw, task)
        instructions = (
            'Read only the provided original image pixels, never infer missing numbers or use memory. '
            'Do not solve the question; transcribe the relevant raw observations, with region, row/column '
            'and colour labels. Return JSON {"observations":[{"region":"...","text":"..."}],'
            '"uncertain":false,"limitations":"..."}. If characters are unclear set uncertain=true.'
        )
        first = _json_response(FinalResponseMiddleware._invoke(model, [
            SystemMessage(content=instructions),
            HumanMessage(content=[{'type': 'text', 'text': task}, *image_blocks]),
        ]))
        observations = first.get('observations')
        if not isinstance(observations, list) or not observations or not all(
            isinstance(o, dict) and isinstance(o.get('region'), str) and isinstance(o.get('text'), str)
            for o in observations
        ) or not isinstance(first.get('uncertain'), bool):
            raise ValueError('Image reader returned invalid observations')
        if first['uncertain']:
            return json.dumps({'verified': False, 'file_path': file_path, **first}, ensure_ascii=False)
        audit = _json_response(FinalResponseMiddleware._invoke(model, [
            SystemMessage(content=(
                'Audit the proposed transcription against the ORIGINAL IMAGE pixels. '
                'Check every digit, sign, decimal point, colour, row and column. Do not trust the draft. '
                'Return JSON {"verified":true/false,"reason":"specific discrepancies or uncertainty"}. '
                'If any relevant character or classification is wrong or uncertain return verified=false.'
            )),
            HumanMessage(content=[{'type': 'text', 'text': f'Task: {task}\nDraft: {json.dumps(first, ensure_ascii=False)}'}, *image_blocks]),
        ]))
        if not isinstance(audit.get('verified'), bool):
            raise ValueError('Image auditor returned no boolean verification')
        return json.dumps({'verified': audit['verified'], 'file_path': file_path,
                           'sha256': hashlib.sha256(raw).hexdigest(), 'task': task,
                           'observations': observations, 'audit_reason': audit.get('reason', '')}, ensure_ascii=False)

    return inspect_image
