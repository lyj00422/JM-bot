from __future__ import annotations

import multiprocessing
import traceback
from collections.abc import Callable
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any, TypeVar

from jmbot.core.jm_service import AlbumJobResult, DownloadRequest, DownloadSettings, run_album_job


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

    try:
        ready = wait([parent_connection, process.sentinel], timeout_seconds)
        if not ready:
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
            raise JobTimeoutError(f"Job exceeded {timeout_seconds:g} seconds")

        if parent_connection in ready or parent_connection.poll():
            try:
                status, payload = parent_connection.recv()
            except EOFError:
                process.join()
                raise RuntimeError(f"Job process exited without a result, exit code: {process.exitcode}") from None

            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()

            if status == "ok":
                return payload

            exc_type, message, child_traceback = payload
            raise RuntimeError(f"{exc_type}: {message}\n{child_traceback}")

        process.join()
        raise RuntimeError(f"Job process exited without a result, exit code: {process.exitcode}")
    finally:
        parent_connection.close()


def run_album_job_with_timeout(
    request: str | DownloadRequest,
    download_settings: DownloadSettings,
    output_root: str | Path,
    *,
    timeout_seconds: float,
) -> AlbumJobResult:
    """Run the full album job in an isolated process with a hard timeout."""

    return run_with_timeout(
        run_album_job,
        request,
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
