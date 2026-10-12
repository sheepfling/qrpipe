"""Named animated-QR receiver protocols supported by qrpipe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class StreamProtocol:
    """A wire format plus the receiver a person needs to use it."""

    name: str
    label: str
    receiver_name: str
    receiver_url: str
    experimental: bool = False


DECIMEN_PROTOCOL: Final = StreamProtocol(
    name="decimen",
    label="Decimen",
    receiver_name="Decimen",
    receiver_url="https://optical-transfer.tongatron.org/",
)
QRF_PROTOCOL: Final = StreamProtocol(
    name="qrf",
    label="QRF 3.0",
    receiver_name="QRF",
    receiver_url="https://qr-transfer.github.io/qrf/vdf-qr-decoder.html",
    experimental=True,
)
STREAM_PROTOCOLS: Final = (DECIMEN_PROTOCOL, QRF_PROTOCOL)


def stream_protocol_names() -> tuple[str, ...]:
    """Return CLI-safe protocol names in their stable display order."""
    return tuple(protocol.name for protocol in STREAM_PROTOCOLS)


def get_stream_protocol(name: str) -> StreamProtocol:
    """Look up a supported protocol by its CLI name."""
    for protocol in STREAM_PROTOCOLS:
        if protocol.name == name:
            return protocol
    msg = f"unsupported stream protocol: {name}"
    raise ValueError(msg)
