from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    text = value.strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _normalize_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    return url.rstrip("/")


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _guess_extension(content_type: str, payload: bytes) -> str:
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    return ".img"


def _guess_image_format(content_type: str, payload: bytes) -> str:
    return _guess_extension(content_type, payload).lstrip(".") or "png"


def _data_url(content_type: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _extract_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _png_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 24 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    width, height = struct.unpack(">II", payload[16:24])
    if width > 0 and height > 0:
        return width, height
    return None


def _jpeg_dimensions(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 4 or not payload.startswith(b"\xff\xd8"):
        return None
    offset = 2
    payload_len = len(payload)
    while offset + 9 < payload_len:
        if payload[offset] != 0xFF:
            offset += 1
            continue
        marker = payload[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > payload_len:
            break
        segment_len = int.from_bytes(payload[offset : offset + 2], "big")
        if segment_len < 2 or offset + segment_len > payload_len:
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(payload[offset + 3 : offset + 5], "big")
            width = int.from_bytes(payload[offset + 5 : offset + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        offset += segment_len
    return None


def _image_dimensions(payload: bytes, content_type: str, metadata: dict[str, Any]) -> tuple[int, int] | None:
    width = _extract_int(metadata.get("width"))
    height = _extract_int(metadata.get("height"))
    if width and height:
        return width, height
    if "png" in content_type:
        return _png_dimensions(payload)
    if "jpeg" in content_type or "jpg" in content_type:
        return _jpeg_dimensions(payload)
    return _png_dimensions(payload) or _jpeg_dimensions(payload)


def _compress_for_vl(
    payload: bytes,
    content_type: str,
    *,
    max_dim_px: int,
    jpeg_quality: int,
) -> tuple[bytes, str]:
    command = shutil.which("magick") or shutil.which("convert")
    if not command:
        return payload, content_type

    suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
    with tempfile.TemporaryDirectory(prefix="pikvm-vl-") as tmpdir:
        src = Path(tmpdir) / f"source{suffix}"
        dst = Path(tmpdir) / "vl.jpg"
        src.write_bytes(payload)

        if Path(command).name == "magick":
            cmd = [
                command,
                str(src),
                "-resize",
                f"{max_dim_px}x{max_dim_px}>",
                "-quality",
                str(jpeg_quality),
                str(dst),
            ]
        else:
            cmd = [
                command,
                str(src),
                "-resize",
                f"{max_dim_px}x{max_dim_px}>",
                "-quality",
                str(jpeg_quality),
                str(dst),
            ]
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            reduced = dst.read_bytes()
            if reduced and len(reduced) < len(payload):
                return reduced, "image/jpeg"
        except Exception:
            return payload, content_type

    return payload, content_type


def _qwen_computer_use_description(*, display_width_px: int = 1000, display_height_px: int = 1000) -> str:
    return (
        "Use a mouse and keyboard to interact with a computer, and take screenshots.\n"
        "* This is an interface to a desktop GUI. You do not have access to a terminal or "
        "applications menu. You must click on desktop icons to start applications.\n"
        "* Some applications may take time to start or process actions, so you may need to "
        "wait and take successive screenshots to see the results of your actions. E.g. if you "
        "click on Firefox and a window doesn't open, try wait and taking another screenshot.\n"
        f"* The screen's resolution is {display_width_px}x{display_height_px}.\n"
        "* Whenever you intend to move the cursor to click on an element like an icon, you "
        "should consult a screenshot to determine the coordinates of the element before moving "
        "the cursor.\n"
        "* If you tried clicking on a program or link but it failed to load, even after "
        "waiting, try adjusting your cursor position so that the tip of the cursor visually "
        "falls on the element that you want to click.\n"
        "* Make sure to click any buttons, links, icons, etc with the cursor tip in the "
        "center of the element. Don't click boxes on their edges."
    )


@dataclass(frozen=True)
class Settings:
    pikvm_url: str
    pikvm_user: str
    pikvm_pass: str
    pikvm_verify_ssl: bool
    capture_dir: Path
    allow_external_models: bool
    vl_api_url: str
    vl_api_key: str
    vl_model: str
    vl_max_tokens: int
    vl_system_prompt: str
    vl_image_max_dim_px: int
    vl_image_jpeg_quality: int
    open_interpreter_http_url: str
    open_interpreter_ws_url: str
    open_interpreter_command: str
    pikvm_mouse_settle_ms: int
    pikvm_mouse_initialize_before_absolute: bool
    pikvm_mouse_click_delay_ms: int

    @classmethod
    def from_env(cls) -> "Settings":
        pikvm_user = os.getenv("PI_KVM_USER", "").strip()
        pikvm_pass = os.getenv("PI_KVM_PASS", "").strip()
        required_pairs: list[tuple[str, str]] = [
            ("PI_KVM_USER", pikvm_user),
            ("PI_KVM_PASS", pikvm_pass),
        ]

        missing = [name for name, value in required_pairs if not value]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {joined}")

        capture_dir = Path(
            os.getenv("PIKVM_CAPTURE_DIR", str(BASE_DIR / "captures"))
        ).expanduser()
        if not capture_dir.is_absolute():
            capture_dir = (BASE_DIR / capture_dir).resolve()

        return cls(
            pikvm_url=_normalize_url(os.getenv("PI_KVM_URL", "https://10.0.0.168")),
            pikvm_user=pikvm_user,
            pikvm_pass=pikvm_pass,
            pikvm_verify_ssl=_as_bool(os.getenv("PI_KVM_VERIFY_SSL"), default=False),
            capture_dir=capture_dir,
            allow_external_models=_as_bool(
                os.getenv("PIKVM_ALLOW_EXTERNAL_MODELS"),
                default=False,
            ),
            vl_api_url=os.getenv(
                "VL_API_URL",
                "https://ai.de-auth.io/v1/chat/completions",
            ).strip(),
            vl_api_key=os.getenv(
                "VL_API_KEY",
                os.getenv("LLAMA_API_KEY", ""),
            ).strip(),
            vl_model=os.getenv(
                "VL_MODEL",
                "Qwen/Qwen2.5-VL-72B-Instruct",
            ).strip(),
            vl_max_tokens=_as_int(os.getenv("VL_MAX_TOKENS"), default=220),
            vl_system_prompt=os.getenv(
                "VL_SYSTEM_PROMPT",
                (
                    "You are a PiKVM UI automation vision assistant. "
                    "Be concise and directive. Prefer short bullets or compact paragraphs. "
                    "Focus on the exact user request, visible blockers, and the next action. "
                    "Avoid long summaries, repeated restatements, and speculative commentary."
                ),
            ).strip(),
            vl_image_max_dim_px=_as_int(os.getenv("VL_IMAGE_MAX_DIM_PX"), default=768),
            vl_image_jpeg_quality=_as_int(os.getenv("VL_IMAGE_JPEG_QUALITY"), default=35),
            open_interpreter_http_url=_normalize_http_url(
                os.getenv("OPEN_INTERPRETER_HTTP_URL", "http://127.0.0.1:8000")
            ),
            open_interpreter_ws_url=_normalize_ws_url(
                os.getenv("OPEN_INTERPRETER_WS_URL"),
                http_url=os.getenv("OPEN_INTERPRETER_HTTP_URL", "http://127.0.0.1:8000"),
            ),
            open_interpreter_command=os.getenv(
                "OPEN_INTERPRETER_COMMAND",
                "interpreter --server",
            ).strip(),
            pikvm_mouse_settle_ms=_as_int(os.getenv("PIKVM_MOUSE_SETTLE_MS"), 90),
            pikvm_mouse_initialize_before_absolute=_as_bool(
                os.getenv("PIKVM_MOUSE_INITIALIZE_BEFORE_ABSOLUTE"),
                True,
            ),
            pikvm_mouse_click_delay_ms=_as_int(os.getenv("PIKVM_MOUSE_CLICK_DELAY_MS"), 250),
        )


def _normalize_http_url(url: str) -> str:
    url = url.strip()
    if not url:
        return "http://127.0.0.1:8000"
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def _normalize_ws_url(url: str | None, *, http_url: str) -> str:
    if url and url.strip():
        normalized = url.strip()
    else:
        normalized = _normalize_http_url(http_url)
        if normalized.startswith("https://"):
            normalized = "wss://" + normalized.removeprefix("https://")
        elif normalized.startswith("http://"):
            normalized = "ws://" + normalized.removeprefix("http://")
    return normalized.rstrip("/") + "/"


def _external_models_disabled_message(feature: str) -> str:
    return (
        f"{feature} is disabled in self-directed mode. "
        "This MCP is configured for Codex to inspect PiKVM screenshots directly and drive "
        "the host with the primitive `pikvm_*` tools, without forwarding screenshots or tasks "
        "to external models. Set PIKVM_ALLOW_EXTERNAL_MODELS=true to re-enable the legacy "
        "remote-model path."
    )


def _build_query(params: dict[str, Any] | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if not params:
        return query
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            query[key] = int(value)
        else:
            query[key] = value
    return query


def _normalize_api_response(data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("ok"):
        raise RuntimeError(f"PiKVM API returned an error: {data}")
    result = data.get("result")
    if isinstance(result, dict):
        return result
    return {"value": result}


def _pikvm_client(settings: Settings, *, timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        auth=(settings.pikvm_user, settings.pikvm_pass),
        timeout=timeout,
        verify=settings.pikvm_verify_ssl,
    )


async def _pikvm_json_request(
    settings: Settings,
    method: str,
    route: str,
    *,
    params: dict[str, Any] | None = None,
    content: str | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{settings.pikvm_url}{route}"
