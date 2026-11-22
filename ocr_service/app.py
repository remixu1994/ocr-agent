import asyncio
import hmac
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from .engine import FORMATS, MAX_BYTES, InvalidImage, PaddleEngine, decode_image
from .document import document_payload
from .parse import report_payload

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}")
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def configured_token() -> str:
    file = os.environ.get("OCR_API_TOKEN_FILE")
    return (Path(file).read_text(encoding="ascii").strip() if file
            else os.environ.get("OCR_API_TOKEN", ""))


def error(code: str, status: int, **headers) -> JSONResponse:
    return JSONResponse({"code": code}, status_code=status, headers=NO_STORE | headers)


def scan(engine, body: bytearray, media_type: str, turns: int, crop: int, mode: str) -> dict:
    with decode_image(body, media_type, turns, crop) as image:
        analysis = engine.analyze(image)
        if mode == "report":
            return report_payload(analysis)
        return document_payload(analysis, *image.size)


def create_app(engine_factory=PaddleEngine, get_token=configured_token) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        token = get_token()
        if not TOKEN_PATTERN.fullmatch(token):
            raise RuntimeError("Configure a random OCR_API_TOKEN (or OCR_API_TOKEN_FILE).")
        app.state.token = token
        app.state.slot = asyncio.Lock()
        app.state.engine = await anyio.to_thread.run_sync(engine_factory)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        return bool(TOKEN_PATTERN.fullmatch(token)) and hmac.compare_digest(
            token, app.state.token
        )

    @app.get("/healthz")
    async def health():
        return JSONResponse({"ready": True}, headers=NO_STORE)

    @app.get("/v1/health")
    async def private_health(request: Request):
        if not authorized(request):
            return error("unauthorized", 401, **{"WWW-Authenticate": "Bearer"})
        return JSONResponse({"ready": True, "schema_version": 1}, headers=NO_STORE)

    @app.post("/v1/ocr")
    async def recognize_report(request: Request):
        return await recognize(request, "report")

    @app.post("/v1/documents/recognize")
    async def recognize_document(request: Request):
        return await recognize(request, "document")

    async def recognize(request: Request, mode: str):
        if not authorized(request):
            return error("unauthorized", 401, **{"WWW-Authenticate": "Bearer"})
        media_type = request.headers.get("content-type", "").lower()
        if media_type not in FORMATS or request.headers.get("content-encoding"):
            return error("unsupported_image", 415)
        length = request.headers.get("content-length")
        if length is not None and not re.fullmatch(r"[0-9]{1,12}", length):
            return error("invalid_request", 400)
        if length is not None and int(length) > MAX_BYTES:
            return error("image_too_large", 413)
        turns = request.headers.get("x-ocr-quarter-turns", "0")
        crop = request.headers.get("x-ocr-crop-percent", "0")
        if turns not in ("0", "1", "2", "3") or crop not in ("0", "5", "10", "15"):
            return error("invalid_transform", 400)
        # Admit one job, including its bounded upload; do not queue photos in memory.
        slot = app.state.slot
        if slot.locked():
            return error("busy", 429, **{"Retry-After": "3"})
        async with slot:
            body = bytearray()
            try:
                async with asyncio.timeout(25):
                    async for part in request.stream():
                        if len(body) + len(part) > MAX_BYTES:
                            return error("image_too_large", 413)
                        body.extend(part)
                if not body:
                    return error("invalid_image", 422)
                result = await anyio.to_thread.run_sync(
                    scan, app.state.engine, body, media_type, int(turns), int(crop), mode
                )
                return JSONResponse(result, headers=NO_STORE)
            except TimeoutError:
                return error("upload_timeout", 408)
            except ClientDisconnect:
                return error("upload_interrupted", 400)
            except InvalidImage:
                return error("invalid_image", 422)
            except Exception:
                # Runtime exception details may contain recognized fields or paths.
                return error("ocr_unavailable", 503)
            finally:
                body.clear()

    return app


app = create_app()
