"""Optional offline GIF export and QR readback for QP1 transfers."""

from __future__ import annotations

from io import BytesIO
from math import ceil
from pathlib import Path
from tempfile import NamedTemporaryFile

import zxingcpp
from PIL import Image
from segno import make_qr

from qrpipe.owned import METADATA_INTERVAL, OwnedEncoder
from qrpipe.owned_receive import OwnedReceiver, ReceivedFile
from qrpipe.stream import StreamError

MAX_GIF_FRAMES = 512
MAX_SCAN_FRAMES = 4096


def save_gif(encoder: OwnedEncoder, path: Path, *, fps: int, error: str) -> int:
    """Save one complete, endlessly looping QP1 part as an animated GIF."""
    if path.suffix.casefold() != ".gif":
        msg = "GIF output path must end in .gif"
        raise StreamError(msg)
    count = encoder.block_count * 2
    frame_count = count + ceil(count / METADATA_INTERVAL)
    if frame_count > MAX_GIF_FRAMES:
        msg = f"GIF needs {frame_count} frames; limit is {MAX_GIF_FRAMES}; use the live sender"
        raise StreamError(msg)
    try:
        qr_codes = tuple(
            make_qr(encoder.frame(index), error=error, boost_error=False)
            for index in range(frame_count)
        )
    except (TypeError, ValueError) as exc:
        msg = "QR frames are too dense for GIF at this error level"
        raise StreamError(msg) from exc
    width = max(int(qr.symbol_size(scale=4, border=4)[0]) for qr in qr_codes)
    height = max(int(qr.symbol_size(scale=4, border=4)[1]) for qr in qr_codes)
    images: list[Image.Image] = []
    for qr in qr_codes:
        encoded = BytesIO()
        qr.save(encoded, kind="png", scale=4, border=4)
        encoded.seek(0)
        with Image.open(encoded) as frame:
            canvas = Image.new("1", (width, height), 1)
            canvas.paste(
                frame.convert("1"), ((width - frame.width) // 2, (height - frame.height) // 2)
            )
            images.append(canvas)
    with NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".gif", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        images[0].save(
            temporary_path,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=max(20, round(100 / fps) * 10),
            loop=0,
            disposal=2,
            optimize=False,
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
        for image in images:
            image.close()
    return frame_count


def read_gif(path: Path) -> ReceivedFile:
    """Scan GIF QR frames locally and restore a single verified QP1 file."""
    receiver = OwnedReceiver()
    try:
        with Image.open(path) as animation:
            if animation.format != "GIF":
                msg = "input is not a GIF"
                raise StreamError(msg)
            frame_count = int(getattr(animation, "n_frames", 1))
            if frame_count > MAX_SCAN_FRAMES:
                msg = f"GIF exceeds the {MAX_SCAN_FRAMES}-frame scan limit"
                raise StreamError(msg)
            for index in range(frame_count):
                animation.seek(index)
                barcode = zxingcpp.read_barcode(animation.convert("RGB"))
                if barcode is not None and barcode.format == zxingcpp.BarcodeFormat.QRCode:
                    result = receiver.accept(barcode.text)
                    if result is not None:
                        return result
    except (OSError, EOFError) as exc:
        msg = "GIF could not be read or decoded"
        raise StreamError(msg) from exc
    msg = "GIF does not contain enough valid QR frames to restore the file"
    raise StreamError(msg)
