"""Terminal QR rendering helpers for Telethon QR authorization."""

from __future__ import annotations

from datetime import UTC, datetime


def render_terminal_qr(data: str) -> str | None:
    """Render one QR payload into a terminal-safe block string when qrcode is installed."""

    try:
        import qrcode  # type: ignore[import-untyped]
        from qrcode.constants import ERROR_CORRECT_L  # type: ignore[import-untyped]
    except ModuleNotFoundError:
        return None
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    on = "██"
    off = "  "
    return "\n".join("".join(on if cell else off for cell in row) for row in matrix)


def describe_qr_expiry(expires_at: object) -> str | None:
    """Return a short `NNs` expiry hint for one QR login object when available."""

    if not isinstance(expires_at, datetime):
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    now = datetime.now(tz=expires_at.tzinfo)
    remaining = int((expires_at - now).total_seconds())
    if remaining <= 0:
        return None
    return f"{remaining}s"
