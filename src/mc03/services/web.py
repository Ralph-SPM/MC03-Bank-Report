"""FastAPI app factory and the three-screen RCBC demo portal.

The web layer owns request validation and in-memory run lifecycle wiring.  It
never stores session state and it delegates spreadsheet parsing, processing,
rendering, and archive packaging to the corresponding service/domain modules.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from mc03.domain.models import Disposition, ProcessedRow, RunResult
from mc03.persistence.run_store import RunStore
from mc03.services.archive import ArchiveError, build_encrypted_archive
from mc03.services.extraction_gateway import ExtractionGateway
from mc03.services.orchestrator import process_run
from mc03.services.remarks_lab import RemarksLabPipeline, GroqRemarksSummarizer
from engine.summarizer import (
    TwoTierRemarksSummarizer,
    get_summarizer,
    OFFICIAL_RCBC_CSU_OPTIONS,
    OFFICIAL_RCBC_RFD_OPTIONS,
    PRIMARY_CSU_OPTIONS,
    PRIMARY_RFD_OPTIONS,
)
from engine.profiles import (
    load_profiles_data,
    get_active_profile,
    save_profile,
    activate_profile,
    delete_profile,
    reset_profiles_to_default,
    fetch_available_models,
    render_full_prompt,
)
from mc03.services.rendering import RenderError, render_workbooks
from mc03.settings import RuntimeSettings, load_runtime_settings

DEFAULT_RUN_ATTRIBUTION: Final[str] = "DA"
MAX_UPLOAD_BYTES: Final[int] = 50 * 1024 * 1024
UPLOAD_FIELDS: Final[tuple[str, ...]] = ("volare_drr", "field_result", "master_file")
ARTIFACT_KEYS: Final[tuple[str, ...]] = ("bank_csr", "internal_csr", "bank_package")
TEMPLATE_NAME: Final[str] = "RCBC AL_NEW-CSR_TEMPLATE JUNE 2026 (V1).xlsx"
_TEMPLATES_DIR: Final[Path] = Path(__file__).resolve().parent / "templates"
_ZIP_MAGIC: Final[bytes] = b"PK\x03\x04"


class LoopbackOnlyMiddleware(BaseHTTPMiddleware):
    """Refuse requests from source addresses that are not loopback clients."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Allow numeric loopback clients and the Starlette test-client marker."""
        client = request.client
        if client is None or not _is_loopback(client.host):
            return PlainTextResponse(
                "Forbidden: non-loopback source address refused.",
                status_code=403,
            )
        return await call_next(request)


def _is_loopback(host: str) -> bool:
    """Return whether a source host is loopback or the synthetic test client."""
    if host.casefold() == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _looks_like_xlsx(filename: str | None, head: bytes) -> bool:
    """Return whether a filename and leading bytes identify an XLSX upload."""
    return bool(filename and filename.lower().endswith(".xlsx") and head.startswith(_ZIP_MAGIC))


def _is_readable_xlsx(data: bytes) -> bool:
    """Return whether *data* opens as a valid OOXML workbook."""
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True)
        workbook.close()
    except Exception:
        return False
    return True


def _parse_iso_date(raw: str | None) -> date | None:
    """Parse an ISO calendar date, returning ``None`` for missing/invalid input."""
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


async def _validate_upload(
    upload: UploadFile | None, field_name: str
) -> tuple[bytes | None, str | None]:
    """Validate one required XLSX upload and return its bytes or a field error."""
    if upload is None or not upload.filename:
        return None, f"{field_name} is required; please choose a .xlsx file."
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return None, f"{field_name} exceeds the 50 MB size limit."
    if not _looks_like_xlsx(upload.filename, data[: len(_ZIP_MAGIC)]):
        return None, f"{field_name} must be a .xlsx file."
    if not _is_readable_xlsx(data):
        return None, f"{field_name} could not be read as a valid .xlsx file."
    return data, None


def _form_str(value: object) -> str:
    """Coerce a form value to a stripped string."""
    return value.strip() if isinstance(value, str) else ""


def _render_template(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    context: dict[str, object],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """Render a portal template with a stable request context."""
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _error_context(message: str) -> dict[str, object]:
    """Build a consistent not-found/processing error context."""
    return {"error": message}


def _run_summary(result: RunResult) -> dict[str, int]:
    """Return the reconciliation counts shown by the export screen."""
    return {
        "included": len(result.clean_rows),
        "excluded": result.excluded_count,
        "unresolved": len(result.review_rows),
    }


def _locate_template(settings: RuntimeSettings) -> Path:
    """Resolve the configured fixed template file without accepting uploads."""
    configured_value = settings.template_path
    if configured_value is None:
        raise RenderError("fixed RCBC template is missing or unreadable")
    configured = Path(configured_value)
    if configured.is_file():
        return configured
    named = configured / TEMPLATE_NAME
    if named.is_file():
        return named
    if configured.is_dir():
        candidates = sorted(configured.glob("*.xlsx"))
        if len(candidates) == 1:
            return candidates[0]
    raise RenderError(f"fixed RCBC template is missing or unreadable: {configured}")


def _write_uploads(settings: RuntimeSettings, uploads: dict[str, bytes]) -> dict[str, Path]:
    """Write validated uploads below the protected raw-source directory."""
    storage = settings.initialize_storage()
    paths: dict[str, Path] = {}
    for field_name, data in uploads.items():
        path = storage.raw_sources / f"{uuid4().hex}_{field_name}.xlsx"
        path.write_bytes(data)
        paths[field_name] = path
    return paths


def _remove_uploads(paths: dict[str, Path]) -> None:
    """Best-effort cleanup of per-request source copies after processing."""
    for path in paths.values():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A cleanup failure must not turn a completed run into a failed HTTP
            # response; the paths remain under the protected root.
            continue


def _target_review_index(result: RunResult, raw_target: str) -> int | None:
    """Resolve a form target to exactly one current Review_Only row."""
    if raw_target.isdigit():
        index = int(raw_target)
        if 0 <= index < len(result.review_rows):
            return (
                index
                if result.review_rows[index].disposition is Disposition.REVIEW_ONLY
                else None
            )
        return None
    matches = [
        index
        for index, row in enumerate(result.review_rows)
        if row.disposition is Disposition.REVIEW_ONLY
        and (row.source_row_ref == raw_target or row.ch_code == raw_target)
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_csu(value: str) -> int | None:
    """Parse the review dropdown's rank or sentiment label."""
    normalized = value.strip().casefold()
    labels = {"cp+up": 3, "cp+un": 2, "cn": 1}
    if normalized in labels:
        return labels[normalized]
    try:
        rank = int(normalized)
    except ValueError:
        return None
    return rank if rank in {1, 2, 3} else None


def _apply_review_edit(
    request_form: object, row: ProcessedRow
) -> tuple[ProcessedRow, str | None]:
    """Apply optional CSU/RFD form edits and return a replacement row."""
    get = getattr(request_form, "get", None)
    if not callable(get):
        return row, "The review decision form is invalid."
    csu_value = _form_str(get("csu_rank") or get("csu"))
    updated = row
    if csu_value:
        parsed_csu = _parse_csu(csu_value)
        if parsed_csu is None:
            return row, "csu must be one of CP+UP, CP+UN, CN, or ranks 1–3."
        updated = replace(updated, csu_rank=parsed_csu)
    if "rfd" in request_form:  # type: ignore[operator]
        rfd_value = _form_str(get("rfd"))
        updated = replace(updated, selected_rfd=rfd_value or None)
    return updated, None


def create_demo_app(settings: RuntimeSettings | None = None) -> FastAPI:
    """Build the loopback-only RCBC portal and its process-lifetime services."""
    resolved = settings if settings is not None else load_runtime_settings()
    if resolved.demo_bind.host != "127.0.0.1" or resolved.demo_bind.port != 8000:
        raise ValueError("RCBC demo portal must bind exclusively to 127.0.0.1:8000")
    app = FastAPI(
        title="RCBC Initial Demo Portal",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(LoopbackOnlyMiddleware)
    app.state.settings = resolved
    app.state.run_attribution = DEFAULT_RUN_ATTRIBUTION
    app.state.demo_bind = resolved.demo_bind
    app.state.run_store = RunStore()
    app.state.gateway = ExtractionGateway.from_settings(resolved)
    app.state.process_runner = None
    app.state.renderer = render_workbooks
    app.state.archive_builder = build_encrypted_archive
    app.state.generation_errors = {}
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def render_setup(
        request: Request,
        *,
        values: dict[str, str],
        errors: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        """Render Screen 1 with retained dates and field-specific errors."""
        return _render_template(
            templates,
            request,
            "setup.html",
            {"values": values, "errors": errors or {}},
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    async def run_setup(request: Request) -> HTMLResponse:
        """Render date inputs, three upload inputs, and the Process control."""
        return render_setup(request, values={})

    @app.post("/process")
    async def process(request: Request) -> Response:
        """Validate uploads, execute one run, and redirect to its review queue."""
        form = await request.form()
        raw_from = _form_str(form.get("report_date_from"))
        raw_to = _form_str(form.get("report_date_to"))
        retained = {"report_date_from": raw_from, "report_date_to": raw_to}
        errors: dict[str, str] = {}
        date_from = _parse_iso_date(raw_from)
        date_to = _parse_iso_date(raw_to)
        if date_from is None:
            errors["report_date_from"] = (
                "report_date_from is required and must be a valid calendar date."
            )
        if date_to is None:
            errors["report_date_to"] = (
                "report_date_to is required and must be a valid calendar date."
            )
        if date_from is not None and date_to is not None and date_from > date_to:
            errors["report_date_to"] = "report_date_from must not be later than report_date_to."

        uploads: dict[str, bytes] = {}
        for field_name in UPLOAD_FIELDS:
            candidate = form.get(field_name)
            upload = candidate if isinstance(candidate, UploadFile) else None
            data, upload_error = await _validate_upload(upload, field_name)
            if upload_error is not None:
                errors[field_name] = upload_error
            elif data is not None:
                uploads[field_name] = data
        if errors or date_from is None or date_to is None:
            return render_setup(request, values=retained, errors=errors, status_code=400)

        source_paths: dict[str, Path] = {}
        try:
            source_paths = _write_uploads(resolved, uploads)
            runner = app.state.process_runner or process_run
            result = runner(
                date_from,
                date_to,
                source_paths["volare_drr"],
                source_paths["field_result"],
                source_paths["master_file"],
                app.state.gateway,
                app.state.run_store,
                attribution=app.state.run_attribution,
            )
            if not isinstance(result, RunResult):
                raise TypeError("process runner did not return a RunResult")
            if app.state.run_store.get(result.run_id) is None:
                app.state.run_store.store(result)
        except Exception:
            return render_setup(
                request,
                values=retained,
                errors={"processing": "Processing failed; no review run was created."},
                status_code=500,
            )
        finally:
            _remove_uploads(source_paths)
        return RedirectResponse(f"/review/{result.run_id}", status_code=303)

    @app.get("/review/{run_id}", response_class=HTMLResponse)
    async def review(request: Request, run_id: str) -> HTMLResponse:
        """Render only actionable Review_Only rows for one stored run."""
        result = app.state.run_store.get(run_id)
        if result is None:
            return _render_template(
                templates,
                request,
                "review.html",
                {"run_id": run_id, **_error_context("Run was not found.")},
                status_code=404,
            )
        rows = [
            {
                "index": index,
                "row": row,
                "account_display": row.account_number or "Unmapped",
                "relation_display": row.relation.value,
                "remark": row.sanitized_remark,
                "length": row.remark_length,
            }
            for index, row in enumerate(result.review_rows)
            if row.disposition is Disposition.REVIEW_ONLY
        ]
        return _render_template(
            templates,
            request,
            "review.html",
            {"run_id": run_id, "rows": rows, "unresolved": len(rows)},
        )

    @app.post("/review/{run_id}/decision")
    async def review_decision(run_id: str, request: Request) -> Response:
        """Apply approve, exclude, or edit to exactly one review row."""
        result = app.state.run_store.get(run_id)
        if result is None:
            return PlainTextResponse("Run was not found.", status_code=404)
        form = await request.form()
        raw_target = _form_str(
            form.get("row_id")
            or form.get("row_index")
            or form.get("source_row_ref")
            or form.get("ch_code")
        )
        target_index = _target_review_index(result, raw_target)
        if target_index is None:
            return PlainTextResponse("The target row is invalid.", status_code=400)
        action = _form_str(form.get("action") or form.get("decision")).casefold()
        if action == "save":
            action = "edit"
        if action not in {"approve", "exclude", "edit"}:
            return PlainTextResponse("The review action is invalid.", status_code=400)
        row, edit_error = _apply_review_edit(form, result.review_rows[target_index])
        if edit_error is not None:
            return PlainTextResponse(edit_error, status_code=400)
        if action == "approve":
            row = replace(row, disposition=Disposition.CLEAN)
            result.clean_rows.append(row)
            result.review_rows.pop(target_index)
        elif action == "exclude":
            row = replace(row, disposition=Disposition.EXCLUDED)
            result.excluded_count += 1
            result.review_rows.pop(target_index)
        else:
            result.review_rows[target_index] = row
        app.state.run_store.store(result)
        return RedirectResponse(f"/review/{run_id}", status_code=303)

    @app.get("/export/{run_id}", response_class=HTMLResponse)
    async def export(request: Request, run_id: str) -> HTMLResponse:
        """Render reconciliation counts and the generate/download controls."""
        result = app.state.run_store.get(run_id)
        if result is None:
            return _render_template(
                templates,
                request,
                "export.html",
                {"run_id": run_id, **_error_context("Run was not found.")},
                status_code=404,
            )
        summary = _run_summary(result)
        downloads = [
            {"key": key, "path": result.artifacts.get(key)}
            for key in ARTIFACT_KEYS
            if result.artifacts.get(key)
        ]
        return _render_template(
            templates,
            request,
            "export.html",
            {
                "run_id": run_id,
                "summary": summary,
                "can_generate": summary["unresolved"] == 0,
                "downloads": downloads,
                "error": app.state.generation_errors.get(run_id),
            },
        )

    @app.post("/export/{run_id}/generate")
    async def export_generate(run_id: str, request: Request) -> Response:
        """Render two workbooks and an AES archive only after all review is resolved."""
        del request
        result = app.state.run_store.get(run_id)
        if result is None:
            return PlainTextResponse("Run was not found.", status_code=404)
        if result.review_rows:
            return PlainTextResponse(
                "Unresolved exceptions must be zero before generation.",
                status_code=409,
            )
        rendered: dict[str, str] = {}
        try:
            storage = resolved.initialize_storage()
            template_path = _locate_template(resolved)
            rendered = app.state.renderer(
                result,
                str(template_path),
                str(storage.artifacts),
            )
            if set(rendered) != {"bank_csr", "internal_csr"}:
                raise RenderError("renderer did not return both required workbooks")
            archive_path = storage.artifacts / f"rcbc_bank_package_{uuid4().hex}.zip"
            archive = app.state.archive_builder(
                [rendered["bank_csr"], rendered["internal_csr"]],
                str(archive_path),
                result.date_from,
            )
            result.artifacts = {
                "bank_csr": rendered["bank_csr"],
                "internal_csr": rendered["internal_csr"],
                "bank_package": archive,
            }
            app.state.run_store.store(result)
            app.state.generation_errors.pop(run_id, None)
        except ArchiveError:
            if rendered:
                result.artifacts.update(rendered)
                result.artifacts.pop("bank_package", None)
                app.state.run_store.store(result)
            message = "Archive creation failed; completed workbooks were retained where available."
            app.state.generation_errors[run_id] = message
            return PlainTextResponse(message, status_code=500)
        except (RenderError, OSError, ValueError, TypeError):
            message = (
                "Artifact generation failed; completed workbooks were retained where available."
            )
            app.state.generation_errors[run_id] = message
            if rendered:
                result.artifacts.update(rendered)
                result.artifacts.pop("bank_package", None)
                app.state.run_store.store(result)
            return PlainTextResponse(message, status_code=500)
        return RedirectResponse(f"/export/{run_id}", status_code=303)

    @app.get("/download/{run_id}/{artifact}")
    async def download(run_id: str, artifact: str) -> Response:
        """Stream one of exactly three completed artifacts."""
        result = app.state.run_store.get(run_id)
        if result is None:
            return PlainTextResponse("Run was not found.", status_code=404)
        if artifact not in ARTIFACT_KEYS:
            return PlainTextResponse("The requested artifact is not available.", status_code=404)
        raw_path = result.artifacts.get(artifact)
        if not raw_path:
            return PlainTextResponse("The requested artifact is not available.", status_code=404)
        path = Path(raw_path)
        if not path.is_file():
            return PlainTextResponse("The requested artifact is not available.", status_code=404)
        media_type = (
            "application/zip"
            if artifact == "bank_package"
            else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/remarks-lab", response_class=HTMLResponse)
    async def remarks_lab_view(request: Request) -> Response:
        """Render the Remarks Intelligence Lab upload form or cached analysis."""
        index_file = Path("frontend/dist/index.html")
        if index_file.is_file() and request.query_params.get("spa"):
            return FileResponse(index_file)

        two_tier = get_summarizer()
        raw_test_mode = request.query_params.get("test_mode", "").lower()
        test_mode = raw_test_mode in ("true", "1", "on", "yes", "") or True
        return templates.TemplateResponse(
            request,
            "remarks_lab.html",
            {
                "request": request,
                "active_step": "remarks_lab",
                "report": None,
                "date_from": request.query_params.get("date_from", ""),
                "date_to": request.query_params.get("date_to", ""),
                "test_mode": test_mode,
                "run_ai": True,
                "litellm_configured": two_tier.is_available,
                "model_tagalog": two_tier.model_tagalog,
                "model_english": two_tier.model_english,
                "groq_configured": two_tier.is_available,
                "groq_model": f"{two_tier.model_tagalog} / {two_tier.model_english}",
                "groq_batch": True,
            },
        )

    @app.post("/remarks-lab/analyze", response_class=HTMLResponse)
    async def remarks_lab_analyze(request: Request) -> Response:
        """Parse FIELD RSULT workbook and render benchmark comparison view."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_test_mode = form.get("test_mode")
        test_mode = str(raw_test_mode).lower() in ("true", "1", "on", "yes")
        raw_run_ai = form.get("run_ai")
        run_ai = str(raw_run_ai).lower() in ("true", "1", "on", "yes")

        two_tier = get_summarizer()
        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return templates.TemplateResponse(
                request,
                "remarks_lab.html",
                {
                    "request": request,
                    "active_step": "remarks_lab",
                    "report": None,
                    "date_from": raw_date_from,
                    "date_to": raw_date_to,
                    "test_mode": test_mode,
                    "error": "Please provide a valid Excel workbook (.xlsx, .xls) or Test Mode CSV (.csv).",
                    "litellm_configured": two_tier.is_available,
                    "model_tagalog": two_tier.model_tagalog,
                    "model_english": two_tier.model_english,
                    "groq_configured": two_tier.is_available,
                    "groq_model": f"{two_tier.model_tagalog} / {two_tier.model_english}",
                    "groq_batch": True,
                },
                status_code=400,
            )

        contents = await workbook_upload.read()
        if not contents:
            return templates.TemplateResponse(
                request,
                "remarks_lab.html",
                {
                    "request": request,
                    "active_step": "remarks_lab",
                    "report": None,
                    "date_from": raw_date_from,
                    "date_to": raw_date_to,
                    "test_mode": test_mode,
                    "error": "The uploaded file is empty.",
                    "litellm_configured": two_tier.is_available,
                    "model_tagalog": two_tier.model_tagalog,
                    "model_english": two_tier.model_english,
                    "groq_configured": two_tier.is_available,
                    "groq_model": f"{two_tier.model_tagalog} / {two_tier.model_english}",
                    "groq_batch": True,
                },
                status_code=400,
            )

        try:
            pipeline = RemarksLabPipeline(two_tier_summarizer=two_tier)
            report = pipeline.process_file(contents, date_from=date_from, date_to=date_to, test_mode=test_mode, run_ai=run_ai)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return templates.TemplateResponse(
                request,
                "remarks_lab.html",
                {
                    "request": request,
                    "active_step": "remarks_lab",
                    "report": None,
                    "date_from": raw_date_from,
                    "date_to": raw_date_to,
                    "test_mode": test_mode,
                    "run_ai": run_ai,
                    "error": f"Failed to process workbook ({type(exc).__name__}): {exc}",
                    "litellm_configured": two_tier.is_available,
                    "model_tagalog": two_tier.model_tagalog,
                    "model_english": two_tier.model_english,
                    "groq_configured": two_tier.is_available,
                    "groq_model": f"{two_tier.model_tagalog} / {two_tier.model_english}",
                    "groq_batch": True,
                },
                status_code=400,
            )

        return templates.TemplateResponse(
            request,
            "remarks_lab.html",
            {
                "request": request,
                "active_step": "remarks_lab",
                "report": report,
                "date_from": raw_date_from,
                "date_to": raw_date_to,
                "test_mode": test_mode,
                "run_ai": run_ai,
                "litellm_configured": two_tier.is_available,
                "model_tagalog": two_tier.model_tagalog,
                "model_english": two_tier.model_english,
                "groq_configured": two_tier.is_available,
                "groq_model": f"{two_tier.model_tagalog} / {two_tier.model_english}",
                "groq_batch": True,
            },
        )

    @app.post("/remarks-lab/export")
    async def remarks_lab_export(request: Request) -> Response:
        """Process FIELD RSULT workbook and export bank-ready Excel file directly."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_test_mode = form.get("test_mode")
        test_mode = str(raw_test_mode).lower() in ("true", "1", "on", "yes")

        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return PlainTextResponse("Please provide an Excel file (.xlsx, .xls) or CSV (.csv)", status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return PlainTextResponse("File is empty", status_code=400)

        pipeline = RemarksLabPipeline()
        report = pipeline.process_file(contents, date_from=date_from, date_to=date_to, test_mode=test_mode)
        output_buffer = pipeline.export_to_excel(report)

        filename = f"RCBC_FIELD_EVALUATED_{date_from.strftime('%Y%m%d') if date_from else 'ALL'}.xlsx"
        return Response(
            content=output_buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/remarks-lab/finetune-prompt")
    async def remarks_lab_finetune_prompt(request: Request) -> Response:
        """
        Analyze human reviewer feedback on remarks and use AI to decide how
        to fine-tune the LLM summarization prompt.
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

        items = payload.get("items", [])
        valid_items = [
            it for it in items
            if isinstance(it, dict) and str(it.get("feedback", "")).strip()
        ]

        if not valid_items:
            return JSONResponse({
                "error": "No feedback provided. Please enter feedback for at least one row."
            }, status_code=400)

        summarizer = GroqRemarksSummarizer()

        feedback_digest = "\n".join([
            f"Example #{idx + 1} (Row: #{it.get('row_index', idx + 1)}, Acct: {it.get('account_number', 'N/A')}):\n"
            f"  Raw Remark: {it.get('raw_remarks', '')}\n"
            f"  Current Cleaned Remark: {it.get('cleaned_remarks', '')}\n"
            f"  Reviewer Feedback / Correction: {it.get('feedback', '')}\n"
            for idx, it in enumerate(valid_items)
        ])

        current_system_prompt = (
            "You are an expert Data Analyst summarizing field collection notes for RCBC bank reports.\n"
            "Strict Rules:\n"
            "1. Summarize the field note in concise Taglish or English preserving WHAT was confirmed or stated.\n"
            "2. Do NOT prefix or include the borrower or user's name; provide ONLY the summarized remark.\n"
            "3. Maximum total output length: strictly 140 characters.\n"
            "4. Output ONLY the raw summary sentence. Do NOT include markdown, quotes, labels, or prefixes.\n"
            "5. Never include bank-prohibited tags: BCAL, BKAL, L3, INB, OBD, SRC, or phone numbers."
        )

        meta_prompt = (
            f"You are an AI Prompt Optimization and Fine-Tuning Specialist for banking collection remarks summarization.\n"
            f"A human reviewer has reviewed the current AI summaries and provided specific feedback/corrections on {len(valid_items)} rows.\n\n"
            f"CURRENT SYSTEM PROMPT:\n\"\"\"\n{current_system_prompt}\n\"\"\"\n\n"
            f"REVIEWER FEEDBACK EXAMPLES:\n{feedback_digest}\n\n"
            f"TASK:\n"
            f"1. Diagnose the Systematic Issues: What common pitfalls or unmet expectations did the reviewer point out?\n"
            f"2. Recommended Prompt Adjustments: What specific rules or guardrails must be added, removed, or emphasized?\n"
            f"3. Proposed Fine-Tuned System Prompt: Provide the exact full text for the new, fine-tuned System Prompt that incorporates this feedback.\n\n"
            f"Structure your response clearly with markdown headings:\n"
            f"### 1. Systematic Feedback Diagnosis\n"
            f"### 2. Rule & Guardrail Adjustments\n"
            f"### 3. Proposed Fine-Tuned System Prompt\n"
        )

        if summarizer.is_available:
            try:
                headers = {
                    "Authorization": f"Bearer {summarizer.api_key}",
                    "Content-Type": "application/json",
                }
                groq_payload = {
                    "model": summarizer.model,
                    "messages": [
                        {"role": "system", "content": "You are a prompt engineering and LLM fine-tuning expert specializing in banking data processing."},
                        {"role": "user", "content": meta_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1024,
                }
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{summarizer.base_url}/chat/completions",
                        headers=headers,
                        json=groq_payload,
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    ai_analysis = choices[0].get("message", {}).get("content", "") if choices else ""
                else:
                    ai_analysis = _generate_heuristic_finetune(
                        valid_items, current_system_prompt, api_error=f"HTTP {resp.status_code}: {resp.text}"
                    )
            except Exception as e:
                ai_analysis = _generate_heuristic_finetune(valid_items, current_system_prompt, api_error=str(e))
        else:
            ai_analysis = _generate_heuristic_finetune(valid_items, current_system_prompt)

        structured_rules = _extract_structured_rules(valid_items)

        return JSONResponse({
            "total_feedback_items": len(valid_items),
            "ai_analysis": ai_analysis,
            "csu_rfd_rules": structured_rules["csu_rfd_rules"],
            "summary_rules": structured_rules["summary_rules"],
            "items_processed": valid_items,
        })

    @app.post("/remarks-lab/apply-rules")
    async def remarks_lab_apply_rules(request: Request) -> Response:
        """
        Persist reviewer-tuned CSU/RFD and summary rules into custom_prompt_rules.json
        to immediately apply them to active LLM models.
        """
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

        csu_rfd_rules = payload.get("csu_rfd_rules", [])
        summary_rules = payload.get("summary_rules", [])
        if not isinstance(csu_rfd_rules, list):
            csu_rfd_rules = []
        if not isinstance(summary_rules, list):
            summary_rules = []

        cleaned_csu_rfd = [str(r).strip() for r in csu_rfd_rules if str(r).strip()]
        cleaned_summary = [str(r).strip() for r in summary_rules if str(r).strip()]

        data_to_store = {
            "csu_rfd_rules": cleaned_csu_rfd,
            "summary_rules": cleaned_summary,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        target_path = Path("storage/custom_prompt_rules.json")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data_to_store, f, indent=2, ensure_ascii=False)

        return JSONResponse({
            "status": "success",
            "message": "Custom prompt rules saved and applied to active models.",
            "applied_rules": data_to_store,
        })

    @app.get("/api/remarks/taxonomies")
    async def get_taxonomies() -> Response:
        """Returns official RCBC CSUs and RFDs for frontend dropdowns."""
        return JSONResponse({
            "csu_options": OFFICIAL_RCBC_CSU_OPTIONS,
            "rfd_options": OFFICIAL_RCBC_RFD_OPTIONS,
            "primary_csu": PRIMARY_CSU_OPTIONS,
            "primary_rfd": PRIMARY_RFD_OPTIONS,
        })

    @app.get("/api/remarks/models")
    async def get_remarks_models() -> Response:
        """Returns list of models available for selection from LiteLLM proxy."""
        models = await fetch_available_models()
        return JSONResponse({"models": models})

    @app.get("/api/remarks/profiles")
    async def get_remarks_profiles() -> Response:
        """Returns all configured prompt profiles and active profile id."""
        data = load_profiles_data()
        return JSONResponse(data)

    @app.post("/api/remarks/profiles")
    async def post_remarks_profiles(request: Request) -> Response:
        """Saves a profile (update/create) or sets active profile."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

        # Check if activating an existing profile
        action = payload.get("action")
        if action == "activate":
            profile_id = str(payload.get("profile_id", "")).strip()
            if not profile_id:
                return JSONResponse({"error": "profile_id is required to activate"}, status_code=400)
            try:
                activated = activate_profile(profile_id)
                get_summarizer().reload_active_profile()
                return JSONResponse({
                    "status": "success",
                    "active_profile": activated,
                    "profiles_data": load_profiles_data(),
                })
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=400)

        # Otherwise saving/updating a profile
        profile = payload.get("profile") or payload
        set_active = payload.get("set_active", True)
        try:
            saved = save_profile(profile, set_active=set_active)
            get_summarizer().reload_active_profile()
            return JSONResponse({
                "status": "success",
                "saved_profile": saved,
                "profiles_data": load_profiles_data(),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.delete("/api/remarks/profiles/{profile_id}")
    async def delete_remarks_profile(profile_id: str) -> Response:
        """Deletes a custom profile."""
        try:
            deleted = delete_profile(profile_id)
            get_summarizer().reload_active_profile()
            return JSONResponse({
                "status": "success",
                "deleted": deleted,
                "profiles_data": load_profiles_data(),
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/remarks/profiles/reset")
    async def reset_remarks_profiles() -> Response:
        """Resets profiles back to factory default configuration."""
        data = reset_profiles_to_default()
        get_summarizer().reload_active_profile()
        return JSONResponse({
            "status": "success",
            "message": "Reset all profiles to factory defaults.",
            "profiles_data": data,
        })

    @app.post("/api/remarks/prompt-preview")
    async def preview_remarks_prompt(request: Request) -> Response:
        """Renders the assembled prompt preview."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        instructions = payload.get("system_instructions", "")
        rendered = render_full_prompt(instructions=instructions)
        return JSONResponse({"rendered_prompt": rendered})

    @app.post("/api/remarks/process")
    async def api_remarks_process(request: Request) -> Response:
        """Production Mode: Parse FIELD RSULT and run AI/Rule pipeline in Real Mode."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_run_ai = form.get("run_ai")
        if raw_run_ai is None or raw_run_ai == "":
            run_ai = True
        else:
            run_ai = str(raw_run_ai).lower() not in ("false", "0", "off", "no")

        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return JSONResponse({"error": "Please provide a valid Excel (.xlsx, .xls) or CSV (.csv) file."}, status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return JSONResponse({"error": "The uploaded file is empty."}, status_code=400)

        try:
            two_tier = get_summarizer()
            two_tier.reload_active_profile()
            pipeline = RemarksLabPipeline(two_tier_summarizer=two_tier)
            report = pipeline.process_file(contents, date_from=date_from, date_to=date_to, test_mode=False, run_ai=run_ai)
            return JSONResponse({
                "success": True,
                "total_rows": report.total_rows,
                "sanitized_tag_count": report.sanitized_tag_count,
                "char_overflow_prevented_count": report.char_overflow_prevented_count,
                "english_count": report.english_count,
                "tagalog_count": report.tagalog_count,
                "detection_latency_ms": report.detection_latency_ms,
                "ai_summarized_count": report.ai_summarized_count,
                "rule_based_fallback_count": report.rule_based_fallback_count,
                "model_tagalog": report.model_tagalog,
                "model_english": report.model_english,
                "rows": [_serialize_row_result(r) for r in report.rows],
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return JSONResponse({"error": f"Failed to process workbook: {exc}"}, status_code=500)

    @app.post("/api/remarks/test")
    async def api_remarks_test(request: Request) -> Response:
        """Pure Test Mode: Parse FIELD RSULT, benchmark against ground truth, and calculate agreement %."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_run_ai = form.get("run_ai")
        if raw_run_ai is None or raw_run_ai == "":
            run_ai = True
        else:
            run_ai = str(raw_run_ai).lower() not in ("false", "0", "off", "no")

        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return JSONResponse({"error": "Please provide a valid Excel (.xlsx, .xls) or CSV (.csv) file."}, status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return JSONResponse({"error": "The uploaded file is empty."}, status_code=400)

        try:
            two_tier = get_summarizer()
            two_tier.reload_active_profile()
            pipeline = RemarksLabPipeline(two_tier_summarizer=two_tier)
            report = pipeline.process_file(contents, date_from=date_from, date_to=date_to, test_mode=True, run_ai=run_ai)
            return JSONResponse({
                "success": True,
                "total_rows": report.total_rows,
                "sanitized_tag_count": report.sanitized_tag_count,
                "csu_accuracy_pct": report.csu_accuracy_pct,
                "rfd_accuracy_pct": report.rfd_accuracy_pct,
                "char_overflow_prevented_count": report.char_overflow_prevented_count,
                "english_count": report.english_count,
                "tagalog_count": report.tagalog_count,
                "detection_latency_ms": report.detection_latency_ms,
                "discrepancy_count": report.discrepancy_count,
                "rows_with_ground_truth": report.rows_with_ground_truth,
                "representative_count": report.representative_count,
                "informant_count": report.informant_count,
                "cardholder_count": report.cardholder_count,
                "ai_summarized_count": report.ai_summarized_count,
                "rule_based_fallback_count": report.rule_based_fallback_count,
                "model_tagalog": report.model_tagalog,
                "model_english": report.model_english,
                "rows": [_serialize_row_result(r) for r in report.rows],
            })
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return JSONResponse({"error": f"Failed to test workbook: {exc}"}, status_code=500)

    @app.post("/api/remarks/process-stream")
    async def api_remarks_process_stream(request: Request) -> Response:
        """Stream real-time progress and final result of Field Remarks processing via SSE."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_run_ai = form.get("run_ai")
        if raw_run_ai is None or raw_run_ai == "":
            run_ai = True
        else:
            run_ai = str(raw_run_ai).lower() not in ("false", "0", "off", "no")

        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return JSONResponse({"error": "Please provide a valid Excel (.xlsx, .xls) or CSV (.csv) file."}, status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return JSONResponse({"error": "The uploaded file is empty."}, status_code=400)

        async def sse_generator():
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def progress_cb(evt: dict[str, Any]):
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "progress", **evt})
                except Exception:
                    pass

            def run_sync_pipeline():
                try:
                    two_tier = get_summarizer()
                    two_tier.reload_active_profile()
                    pipeline = RemarksLabPipeline(two_tier_summarizer=two_tier)
                    report = pipeline.process_file(
                        contents,
                        date_from=date_from,
                        date_to=date_to,
                        test_mode=False,
                        run_ai=run_ai,
                        progress_callback=progress_cb,
                    )
                    payload = {
                        "type": "complete",
                        "data": {
                            "success": True,
                            "total_rows": report.total_rows,
                            "sanitized_tag_count": report.sanitized_tag_count,
                            "char_overflow_prevented_count": report.char_overflow_prevented_count,
                            "english_count": report.english_count,
                            "tagalog_count": report.tagalog_count,
                            "detection_latency_ms": report.detection_latency_ms,
                            "ai_summarized_count": report.ai_summarized_count,
                            "rule_based_fallback_count": report.rule_based_fallback_count,
                            "model_tagalog": report.model_tagalog,
                            "model_english": report.model_english,
                            "rows": [_serialize_row_result(r) for r in report.rows],
                        },
                    }
                    loop.call_soon_threadsafe(queue.put_nowait, payload)
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(exc)})

            import concurrent.futures
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = loop.run_in_executor(executor, run_sync_pipeline)

            while not future.done() or not queue.empty():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"

            executor.shutdown(wait=False)

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    @app.post("/api/remarks/test-stream")
    async def api_remarks_test_stream(request: Request) -> Response:
        """Stream real-time progress and final benchmark result for Remarks Lab via SSE."""
        form = await request.form()
        workbook_upload = form.get("workbook")
        raw_date_from = _form_str(form.get("date_from"))
        raw_date_to = _form_str(form.get("date_to"))
        date_from = _parse_iso_date(raw_date_from)
        date_to = _parse_iso_date(raw_date_to)
        raw_run_ai = form.get("run_ai")
        if raw_run_ai is None or raw_run_ai == "":
            run_ai = True
        else:
            run_ai = str(raw_run_ai).lower() not in ("false", "0", "off", "no")

        upload_name = (getattr(workbook_upload, "filename", None) or "").lower()
        is_valid_file = isinstance(workbook_upload, UploadFile) and (
            upload_name.endswith(".xlsx") or upload_name.endswith(".xls") or upload_name.endswith(".csv")
        )
        if not is_valid_file:
            return JSONResponse({"error": "Please provide a valid Excel (.xlsx, .xls) or CSV (.csv) file."}, status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return JSONResponse({"error": "The uploaded file is empty."}, status_code=400)

        async def sse_generator():
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def progress_cb(evt: dict[str, Any]):
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "progress", **evt})
                except Exception:
                    pass

            def run_sync_pipeline():
                try:
                    two_tier = get_summarizer()
                    two_tier.reload_active_profile()
                    pipeline = RemarksLabPipeline(two_tier_summarizer=two_tier)
                    report = pipeline.process_file(
                        contents,
                        date_from=date_from,
                        date_to=date_to,
                        test_mode=True,
                        run_ai=run_ai,
                        progress_callback=progress_cb,
                    )
                    payload = {
                        "type": "complete",
                        "data": {
                            "success": True,
                            "total_rows": report.total_rows,
                            "sanitized_tag_count": report.sanitized_tag_count,
                            "csu_accuracy_pct": report.csu_accuracy_pct,
                            "rfd_accuracy_pct": report.rfd_accuracy_pct,
                            "char_overflow_prevented_count": report.char_overflow_prevented_count,
                            "english_count": report.english_count,
                            "tagalog_count": report.tagalog_count,
                            "detection_latency_ms": report.detection_latency_ms,
                            "discrepancy_count": report.discrepancy_count,
                            "rows_with_ground_truth": report.rows_with_ground_truth,
                            "representative_count": report.representative_count,
                            "informant_count": report.informant_count,
                            "cardholder_count": report.cardholder_count,
                            "ai_summarized_count": report.ai_summarized_count,
                            "rule_based_fallback_count": report.rule_based_fallback_count,
                            "model_tagalog": report.model_tagalog,
                            "model_english": report.model_english,
                            "rows": [_serialize_row_result(r) for r in report.rows],
                        },
                    }
                    loop.call_soon_threadsafe(queue.put_nowait, payload)
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "error": str(exc)})

            import concurrent.futures
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = loop.run_in_executor(executor, run_sync_pipeline)

            while not future.done() or not queue.empty():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.25)
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"

            executor.shutdown(wait=False)

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    @app.post("/api/remarks/export-processed")
    async def api_remarks_export_processed(request: Request) -> Response:
        """Export reviewed/edited rows from Remark Processor into bank-compliant Excel file."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

        rows = payload.get("rows", [])
        if not isinstance(rows, list) or not rows:
            return JSONResponse({"error": "No rows to export"}, status_code=400)

        test_mode = bool(payload.get("test_mode", False))
        pipeline = RemarksLabPipeline()
        output_buffer = pipeline.export_processed_rows(rows, test_mode=test_mode)

        prefix = "RCBC_FIELD_EVALUATED" if test_mode else "RCBC_FIELD_PROCESSED"
        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return Response(
            content=output_buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    dist_dir = Path("frontend/dist")
    if dist_dir.is_dir() and (dist_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="frontend-assets")

    @app.get("/remark-processor", response_class=HTMLResponse)
    async def remark_processor_page(request: Request) -> Response:
        index_file = Path("frontend/dist/index.html")
        if index_file.is_file():
            return FileResponse(index_file)
        return templates.TemplateResponse(
            request,
            "remarks_lab.html",
            {
                "request": request,
                "active_step": "remark_processor",
                "report": None,
                "test_mode": False,
            },
        )

    return app


def _serialize_row_result(r: Any) -> dict[str, Any]:
    """Serialize a RowResult dataclass into a JSON-serializable dictionary."""
    return {
        "row_index": getattr(r, "row_index", 0),
        "account_number": getattr(r, "account_number", ""),
        "ch_code": getattr(r, "ch_code", ""),
        "contact_person": getattr(r, "contact_person", ""),
        "contact_relation": getattr(r, "contact_relation", ""),
        "category_label": getattr(r, "category_label", ""),
        "normalized_role": getattr(r, "normalized_role", ""),
        "matched_relation_keyword": getattr(r, "matched_relation_keyword", ""),
        "concat_val": getattr(r, "concat_val", ""),
        "raw_remarks": getattr(r, "raw_remarks", ""),
        "cleaned_remarks": getattr(r, "cleaned_remarks", ""),
        "prohibited_tags_stripped": getattr(r, "prohibited_tags_stripped", []),
        "trimmed_statement": getattr(r, "trimmed_statement", ""),
        "original_char_count": getattr(r, "original_char_count", 0),
        "final_char_count": getattr(r, "final_char_count", 0),
        "truncated": getattr(r, "truncated", False),
        "predicted_csu": getattr(r, "predicted_csu", ""),
        "predicted_rfd": getattr(r, "predicted_rfd", ""),
        "detailed_rfd": getattr(r, "detailed_rfd", ""),
        "csu_reasoning": getattr(r, "csu_reasoning", ""),
        "rfd_reasoning": getattr(r, "rfd_reasoning", ""),
        "csu_confidence": getattr(r, "csu_confidence", "high"),
        "csu_alternatives": getattr(r, "csu_alternatives", []),
        "rfd_confidence": getattr(r, "rfd_confidence", "high"),
        "rfd_alternatives": getattr(r, "rfd_alternatives", []),
        "manual_csu": getattr(r, "manual_csu", ""),
        "manual_rfd": getattr(r, "manual_rfd", ""),
        "csu_match": getattr(r, "csu_match", False),
        "rfd_match": getattr(r, "rfd_match", False),
        "discrepancy_flag": getattr(r, "discrepancy_flag", False),
        "row_date": getattr(r, "row_date", ""),
        "raw_message": getattr(r, "raw_message", ""),
        "ai_summarized": getattr(r, "ai_summarized", False),
        "trim_method": getattr(r, "trim_method", "none"),
        "detected_language": getattr(r, "detected_language", "ENGLISH"),
        "language_route": getattr(r, "language_route", "ENGLISH"),
        "model_used": getattr(r, "model_used", ""),
        "classification_source": getattr(r, "classification_source", "RULE"),
    }


def _extract_structured_rules(items: list[dict]) -> dict[str, list[str]]:
    """Synthesize actionable CSU/RFD and summary rules from reviewer feedback items."""
    csu_rfd_rules: list[str] = []
    summary_rules: list[str] = []

    for it in items:
        fb = str(it.get("feedback", "")).strip()
        if not fb:
            continue
        fb_lower = fb.lower()

        # Rule extraction for CSU/RFD
        if "representative refused" in fb_lower:
            r = "Use 'REPRESENTATIVE REFUSED TO DISCLOSE RFD' when contact was with representative and no explicit hardship was disclosed."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)
        if "borrower refused" in fb_lower:
            r = "Use 'BORROWER REFUSED TO DISCLOSE RFD' when contact was with borrower directly and no explicit hardship was disclosed."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)
        if "impound" in fb_lower:
            r = "Never output 'Unit Impounded'; always classify as 'LTO APPREHENSION/NO ORCR/HPG'."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)
        if "moved out" in fb_lower:
            r = "Use 'MOVED OUT' only if explicitly confirmed that borrower previously lived there but relocated."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)
        if "empty" in fb_lower and ("rfd" in fb_lower or "unknown" in fb_lower):
            r = "Leave RFD empty (\"\") if zero information was gathered or borrower is unknown in the area."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)
        if "no client" in fb_lower or "not around" in fb_lower:
            r = "Use 'NO CLIENT/ REPRESENTATIVE' only if client resides there but was not around during visit."
            if r not in csu_rfd_rules:
                csu_rfd_rules.append(r)

        # If feedback directly provides a specific instruction regarding RFD or CSU
        if any(term in fb_lower for term in ("rfd", "csu", "refused", "disclose", "positive", "negative", "recon")) and len(fb) <= 160:
            clean_fb = fb.strip(" .\"'")
            if clean_fb and clean_fb not in csu_rfd_rules:
                csu_rfd_rules.append(clean_fb)

        # Rule extraction for Summary
        if any(w in fb_lower for w in ("short", "length", "char", "overflow", "limit")):
            r = "Restrict sentence length to strictly 100-120 characters."
            if r not in summary_rules:
                summary_rules.append(r)
        if any(w in fb_lower for w in ("name", "borrower", "agent", "ch")):
            r = "Never include borrower, collector, or agent names in the summary."
            if r not in summary_rules:
                summary_rules.append(r)
        if any(w in fb_lower for w in ("date", "amount", "ptp", "pay", "pesos", "peso")):
            r = "Preserve exact payment dates and promise amounts."
            if r not in summary_rules:
                summary_rules.append(r)
        if any(w in fb_lower for w in ("car", "unit", "vehicle", "plate", "garage")):
            r = "Ensure vehicle presence/status is prominently preserved."
            if r not in summary_rules:
                summary_rules.append(r)

    if not summary_rules:
        summary_rules.append("Condense narrative into a single high-density fact-based sentence under 140 chars.")

    return {
        "csu_rfd_rules": csu_rfd_rules,
        "summary_rules": summary_rules,
    }


def _generate_heuristic_finetune(items: list[dict], current_prompt: str, api_error: str | None = None) -> str:
    """Generate structured prompt fine-tuning guidance when LLM API is offline or returns error."""
    feedback_texts = [str(it.get("feedback", "")).strip() for it in items]
    summary_bullets_list = []
    for idx, it in enumerate(items[:6]):
        row_ref = it.get("row_index")
        row_label = f"Row #{row_ref}" if row_ref else f"Row #{idx + 1}"
        acct = it.get("account_number")
        acct_label = f" (Acct: {acct})" if acct else ""
        summary_bullets_list.append(f"- {row_label}{acct_label}: \"{it.get('feedback', '')}\"")
    summary_bullets = "\n".join(summary_bullets_list)

    extra_rules = []
    combined_fb = " ".join(feedback_texts).lower()
    if any(w in combined_fb for w in ("short", "length", "char", "cut", "overflow", "limit")):
        extra_rules.append("- Stricter Length Cap: Restrict sentence length to strictly 100-120 chars to guarantee maximum safety margin under 200 chars.")
    if any(w in combined_fb for w in ("name", "borrower", "client", "agent", "ch")):
        extra_rules.append("- Strict Name Prohibition: Absolutely remove any mention of person names (borrower, agent, caller, or relative names).")
    if any(w in combined_fb for w in ("date", "amount", "ptp", "pay", "pesos", "peso", "when")):
        extra_rules.append("- Concrete Financial Facts: Prioritize keeping exact dates, amounts, and explicit settlement promises over background descriptions.")
    if any(w in combined_fb for w in ("car", "unit", "vehicle", "plate", "garage", "status")):
        extra_rules.append("- Vehicle Condition/Location: Ensure presence/absence of vehicle (e.g. unit in garage, flooded, impounded) is prominently preserved.")
    if not extra_rules:
        extra_rules.append("- Concise Outcome Structure: Condense narrative into a single high-density fact-based sentence.")

    rules_text = "\n".join(extra_rules)
    note = f"> [!NOTE]\n> Generated via local rule synthesis (Groq API error: {api_error})\n\n" if api_error else ""

    return (
        f"{note}### 1. Systematic Feedback Diagnosis\n"
        f"Analyzed {len(items)} reviewer feedback item(s):\n{summary_bullets}\n\n"
        f"### 2. Rule & Guardrail Adjustments\n"
        f"{rules_text}\n\n"
        f"### 3. Proposed Fine-Tuned System Prompt\n"
        f"```text\n"
        f"You are an expert Data Analyst summarizing field collection notes for RCBC bank reports.\n"
        f"Strict Rules:\n"
        f"1. Summarize the field note in concise Taglish or English preserving WHAT was confirmed, promised, or observed.\n"
        f"2. Never include borrower or agent names, phone numbers, or bank-prohibited tags (BCAL, BKAL, L3, INB, OBD, SRC).\n"
        f"3. Maximum output length: strictly 120 characters.\n"
        f"4. User Feedback Directives: {'; '.join(extra_rules)}.\n"
        f"5. Output ONLY the raw summary sentence with no markdown, quotes, labels, or prefixes.\n"
        f"```"
    )


__all__ = [
    "ARTIFACT_KEYS",
    "DEFAULT_RUN_ATTRIBUTION",
    "LoopbackOnlyMiddleware",
    "MAX_UPLOAD_BYTES",
    "create_demo_app",
]
