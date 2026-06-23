from __future__ import annotations

import multiprocessing
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from jmbot.core.jm_service import AlbumJobResult, DownloadSettings, run_album_job


T = TypeVar("T")


class JobTimeoutError(TimeoutError):
    """Raised when an isolated album job exceeds its configured runtime."""


def run_with_timeout(
    func: Callable[..., T],
    *args: Any,
    timeout_seconds: float,
    **kwargs: Any,
) -> T:
    """Run a picklable function in a child process and terminate it on timeout."""

    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    process = context.Process(target=_run_child, args=(child_connection, func, args, kwargs))
    process.start()
    child_connection.close()

    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        raise JobTimeoutError(f"Job exceeded {timeout_seconds:g} seconds")

    if parent_connection.poll():
        status, payload = parent_connection.recv()
        if status == "ok":
            return payload

        exc_type, message, child_traceback = payload
        raise RuntimeError(f"{exc_type}: {message}\n{child_traceback}")

    raise RuntimeError(f"Job process exited without a result, exit code: {process.exitcode}")


def run_album_job_with_timeout(
    album_id: str,
    download_settings: DownloadSettings,
    output_root: str | Path,
    *,
    timeout_seconds: float,
) -> AlbumJobResult:
    """Run the full album job in an isolated process with a hard timeout."""

    return run_with_timeout(
        run_album_job,
        album_id,
        download_settings,
        output_root,
        timeout_seconds=timeout_seconds,
    )


def _run_child(connection: Any, func: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    try:
        connection.send(("ok", func(*args, **kwargs)))
    except BaseException as exc:
        connection.send(("error", (type(exc).__name__, str(exc), traceback.format_exc())))
    finally:
        connection.close()
