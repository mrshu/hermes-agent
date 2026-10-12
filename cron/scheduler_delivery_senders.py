"""Native media and bounded standalone transports for cron delivery.

Transport helpers late-import the delivery orchestrator's config and error seams.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from cron.scheduler_delivery import _TargetDelivery

logger = logging.getLogger("cron.scheduler")

_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _resolve_live_webex_destination(t: Optional[_TargetDelivery], raw_response) -> None:
    """Use the API-resolved DM room for attachments and reply-facing session state."""
    if t is not None and t.platform_name == "webex" and isinstance(raw_response, dict):
        t.chat_id = str(raw_response.get("roomId") or t.chat_id)
        t.thread_id = raw_response.get("parentId", t.thread_id)


def _record_media_delivery_result(job_ref, media_path, result, errors, delivery_target) -> bool:
    """Retain a successful attachment's resolved room, including media-only reports."""
    from cron.scheduler_delivery import _confirm_adapter_delivery, _note_target_error
    if result and not getattr(result, "success", True):
        _note_target_error(
            job_ref,
            f"media send failed for {media_path}: {getattr(result, 'error', 'unknown')}",
            errors,
        )
        return False
    if not _confirm_adapter_delivery(result, job_ref["id"]):
        return False
    _resolve_live_webex_destination(delivery_target, getattr(result, "raw_response", None))
    return True


def _send_media_via_adapter(
    adapter, chat_id: str, media_files: list, metadata: dict | None, loop, job: dict, platform=None,
    *, delivery_target: Optional[_TargetDelivery] = None, delivered_media: Optional[list] = None,
) -> list:
    """Send MEDIA files as native attachments (routed by extension, as in
    _process_message_background). Returns per-file error strings so a dropped attachment surfaces
    in run status, not just the gateway log. ``delivered_media`` captures acknowledged attachments
    independently so a partial delivery still has reply-facing context."""
    from cron.scheduler_delivery import _note_target_error
    from cron import scheduler_script as _script
    from gateway.platforms.base import (
        BasePlatformAdapter, should_send_media_as_audio, validate_media_delivery_path)
    from agent.async_utils import safe_schedule_threadsafe
    job_ref = {"id": job.get("id", "?")}
    errors: list = []
    requested = [(str(p), v) for p, v in (media_files or [])]
    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files)
    # Report paths the safety filter dropped (missing file, denied prefix, strict-mode miss).
    kept = {p for p, _ in media_files}
    for raw_path, _v in requested:
        try:
            dropped = validate_media_delivery_path(raw_path) not in kept
        except Exception:
            dropped = True
        if dropped:
            errors.append(f"attachment dropped by media path policy: {raw_path}")

    route_platform = platform if platform is not None else getattr(adapter, "platform", None)
    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            if should_send_media_as_audio(route_platform, ext, is_voice=_is_voice):
                method, path_kw = "send_voice", "audio_path"
            elif ext in _VIDEO_EXTS:
                method, path_kw = "send_video", "video_path"
            elif ext in _IMAGE_EXTS:
                method, path_kw = "send_image_file", "image_path"
            else:
                method, path_kw = "send_document", "file_path"
            coro = getattr(adapter, method)(
                chat_id=chat_id, metadata=metadata, **{path_kw: media_path})
            future = safe_schedule_threadsafe(coro, loop)
            if future is None:
                _note_target_error(
                    job_ref, f"cannot send media {media_path}: gateway loop unavailable", errors)
                return errors
            try:
                # Large attachments can exceed 30s; configurable via _get_media_send_timeout().
                result = future.result(timeout=_script._get_media_send_timeout())
            except TimeoutError:
                future.cancel()
                raise
            if (_record_media_delivery_result(job_ref, media_path, result, errors, delivery_target)
                    and delivered_media is not None):
                delivered_media.append((media_path, _is_voice))
        except Exception as e:
            # TimeoutError etc. have an empty str(); fall back to the class name.
            _note_target_error(
                job_ref, f"failed to send media {media_path}: {str(e) or type(e).__name__}", errors)
    return errors


def _live_send_media(
    t: _TargetDelivery, media_metadata: dict, media_files: list, delivery_errors: list,
    *, media_only: bool = False,
) -> bool:
    """Send attachments with the text's routing; return whether any was confirmed."""
    routed_media_metadata = dict(media_metadata or {})
    if t.is_relay:
        routed_media_metadata["_relay_logical_platform"] = t.platform.value
        logical_home = t.config.get_home_channel(t.platform)
        if logical_home is not None and logical_home.chat_id == t.chat_id:
            if logical_home.user_id:
                routed_media_metadata["user_id"] = logical_home.user_id
            if logical_home.scope_id:
                routed_media_metadata["scope_id"] = logical_home.scope_id
    delivered_media: list = []
    _media_errors = _send_media_via_adapter(
        t.runtime_adapter, t.chat_id, media_files, routed_media_metadata or None, t.loop, t.job,
        platform=t.platform, delivery_target=t, delivered_media=delivered_media,
    )
    # Surface per-file failures into run status: text delivered but attachment lost is not ok.
    for _me in _media_errors:
        delivery_errors.append(f"{_me} (target {t.where})")
    if media_only:
        from tools.send_message_tool import _describe_media_for_mirror
        t.mirror_text = _describe_media_for_mirror(delivered_media)
    return bool(delivered_media)


def _standalone_send(
    t: _TargetDelivery, content: str, media_files: list) -> tuple[Any, Optional[str]]:
    """Run the standalone sender for one target: ``(result, None)`` or ``(None, error)`` (already
    logged — WARNING for a shutdown race, ERROR with traceback otherwise)."""
    from cron import scheduler as _sched
    from cron.scheduler_delivery import _get_standalone_send_timeout
    from tools.send_message_tool import _send_to_platform
    job = t.job
    shutdown_msg = f"delivery to {t.where} skipped — interpreter is shutting down"

    send_timeout = _get_standalone_send_timeout()

    async def _send():
        # The bound lives inside the coroutine: the running-loop fallback below closes ``coro``
        # unstarted, and a wait_for wrapper created out here would be left never awaited.
        return await asyncio.wait_for(_send_to_platform(
            t.platform, t.pconfig, t.chat_id, content, thread_id=t.thread_id,
            media_files=media_files), timeout=send_timeout)

    def _warned(msg: str) -> tuple[None, str]:
        logger.warning("Job '%s': %s", job["id"], msg)
        return None, msg

    def _failed(e) -> tuple[None, str]:
        msg = f"delivery to {t.where} failed: {e}"
        logger.error("Job '%s': %s", job["id"], msg, exc_info=True)
        return None, msg

    # Interpreter finalizing (SIGTERM/restart/OOM): asyncio.run and a fresh ThreadPoolExecutor both
    # raise "cannot schedule new futures after interpreter shutdown" — warn, not ERROR traceback.
    if _sched._interpreter_shutting_down():
        return _warned(shutdown_msg)
    # The live lane failed closed on an empty payload; standalone senders don't (Telegram returns
    # success=True for empty content WITHOUT an API call) — a phantom delivery would result.
    if not content.strip() and not media_files:
        return _warned(f"standalone send skipped (empty text and no media) for {t.where}")
    coro = _send()
    try:
        return asyncio.run(coro), None
    except TimeoutError:
        # The send may still complete on the gateway loop (the dispatch shield keeps an in-flight
        # send un-cancelled); the run is released instead of waiting on it unbounded (#115469).
        msg = (f"standalone send to {t.where} timed out after {send_timeout}s "
               "(the send may still be in flight)")
        logger.error("Job '%s': %s", job["id"], msg)
        return None, msg
    except RuntimeError as run_err:
        # asyncio.run() refuses inside a running loop; close the unstarted coro, retry in a thread.
        coro.close()
        if _sched._interpreter_shutting_down(run_err):
            return _warned(shutdown_msg)
        # The fallback can itself raise (SMTP, result timeout); catch it or remaining targets skip.
        try:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                # A fresh thread does NOT inherit the profile ContextVars (home override + secret
                # scope); run in the active context or the sender reads the default bot token.
                return pool.submit(contextvars.copy_context().run, asyncio.run, _send()).result(
                    timeout=30), None
            finally:
                pool.shutdown(wait=False)
        except Exception as e:
            if _sched._interpreter_shutting_down(e):
                return _warned(shutdown_msg)
            return _failed(e)
    except Exception as e:
        return _failed(e)
