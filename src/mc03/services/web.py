"""FastAPI app factory and the three-screen RCBC demo portal.

The web layer owns request validation and in-memory run lifecycle wiring.  It
never stores session state and it delegates spreadsheet parsing, processing,
rendering, and archive packaging to the corresponding service/domain modules.
"""

from __future__ import annotations

import io
import ipaddress
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Final
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import FileResponse

from mc03.domain.models import Disposition, ProcessedRow, RunResult
from mc03.persistence.run_store import RunStore
from mc03.services.archive import ArchiveError, build_encrypted_archive
from mc03.services.extraction_gateway import ExtractionGateway
from mc03.services.orchestrator import process_run
from mc03.services.remarks_lab import RemarksLabPipeline, GroqRemarksSummarizer
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
        summarizer = GroqRemarksSummarizer()
        return templates.TemplateResponse(
            request,
            "remarks_lab.html",
            {
                "request": request,
                "active_step": "remarks_lab",
                "report": None,
                "date_from": request.query_params.get("date_from", ""),
                "date_to": request.query_params.get("date_to", ""),
                "groq_configured": summarizer.is_available,
                "groq_model": summarizer.model,
                "groq_batch": summarizer.batch_processing,
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

        summarizer = GroqRemarksSummarizer()
        if not isinstance(workbook_upload, UploadFile):
            return templates.TemplateResponse(
                request,
                "remarks_lab.html",
                {
                    "request": request,
                    "active_step": "remarks_lab",
                    "report": None,
                    "date_from": raw_date_from,
                    "date_to": raw_date_to,
                    "error": "Please provide a valid Excel workbook (.xlsx).",
                    "groq_configured": summarizer.is_available,
                    "groq_model": summarizer.model,
                    "groq_batch": summarizer.batch_processing,
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
                    "error": "The uploaded file is empty.",
                    "groq_configured": summarizer.is_available,
                    "groq_model": summarizer.model,
                    "groq_batch": summarizer.batch_processing,
                },
                status_code=400,
            )

        try:
            pipeline = RemarksLabPipeline(summarizer=summarizer)
            report = pipeline.process_file(contents, date_from=date_from, date_to=date_to)
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
                    "error": f"Failed to process workbook ({type(exc).__name__}): {exc}",
                    "groq_configured": summarizer.is_available,
                    "groq_model": summarizer.model,
                    "groq_batch": summarizer.batch_processing,
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
                "groq_configured": summarizer.is_available,
                "groq_model": summarizer.model,
                "groq_batch": summarizer.batch_processing,
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

        if not isinstance(workbook_upload, UploadFile):
            return PlainTextResponse("Please provide an Excel file (.xlsx)", status_code=400)

        contents = await workbook_upload.read()
        if not contents:
            return PlainTextResponse("File is empty", status_code=400)

        pipeline = RemarksLabPipeline()
        report = pipeline.process_file(contents, date_from=date_from, date_to=date_to)
        output_buffer = pipeline.export_to_excel(report)

        filename = f"RCBC_FIELD_EVALUATED_{date_from.strftime('%Y%m%d') if date_from else 'ALL'}.xlsx"
        return Response(
            content=output_buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


__all__ = [
    "ARTIFACT_KEYS",
    "DEFAULT_RUN_ATTRIBUTION",
    "LoopbackOnlyMiddleware",
    "MAX_UPLOAD_BYTES",
    "create_demo_app",
]
