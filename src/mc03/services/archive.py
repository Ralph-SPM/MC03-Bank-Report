"""AES-256 archive packaging for completed RCBC workbooks."""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import pyzipper  # type: ignore[import-untyped]


class ArchiveError(RuntimeError):
    """Raised when a secure demo archive cannot be produced."""


_MONTH_ABBREVIATIONS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)

def derive_archive_password(run_month: date | datetime | None) -> str:
    """Derive the seven-character ``MONYYYY`` archive password.

    The password is returned only to the archive implementation/caller that
    explicitly requests derivation; the web route and archive builder never log
    or include it in a response or filename.
    """
    if not isinstance(run_month, date | datetime):
        raise ArchiveError("run month/year is unavailable or invalid")
    if run_month.year < 1 or run_month.month < 1 or run_month.month > 12:
        raise ArchiveError("run month/year is unavailable or invalid")
    return _MONTH_ABBREVIATIONS[run_month.month - 1] + f"{run_month.year:04d}"


def _temporary_archive_path(parent: Path) -> Path:
    """Create a temporary archive path beside the requested output."""
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".zip",
        prefix=".mc03-archive-",
        dir=parent,
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def build_encrypted_archive(
    paths: list[str],
    out_zip: str,
    run_month: date | datetime | None,
) -> str:
    """Build a WinZip-compatible AES-256 archive containing all workbooks.

    The archive is written to a temporary sibling and atomically replaced only
    after every input has been added successfully.  Thus a failed archive leaves
    all source workbooks and any prior archive untouched.  The derived password
    never appears in an exception, filename, or emitted response.
    """
    password = derive_archive_password(run_month)
    output = Path(out_zip)
    if password.casefold() in output.name.casefold():
        raise ArchiveError("archive filename must not contain the archive password")
    if not paths:
        raise ArchiveError("archive requires at least one completed workbook")
    input_paths = [Path(path) for path in paths]
    for path in input_paths:
        if not path.is_file():
            raise ArchiveError("completed workbook is unavailable")
        if password.casefold() in path.name.casefold():
            raise ArchiveError("artifact filename must not contain the archive password")

    parent = output.parent
    if not parent.exists() or not parent.is_dir():
        raise ArchiveError("archive output directory is unavailable")
    temporary = _temporary_archive_path(parent)
    try:
        with pyzipper.AESZipFile(
            temporary,
            mode="w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(password.encode("ascii"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            seen_names: set[str] = set()
            for path in input_paths:
                member_name = path.name
                if member_name in seen_names:
                    raise ArchiveError("archive input filenames must be unique")
                seen_names.add(member_name)
                archive.write(path, arcname=member_name)
        os.replace(temporary, output)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError("AES-256 archive creation failed") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return str(output)


__all__ = ["ArchiveError", "build_encrypted_archive", "derive_archive_password"]
