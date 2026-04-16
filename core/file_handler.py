"""
Helix File Handler
Validates, stores, and cleans up uploaded files from Discord and Telegram.
Files are downloaded by their respective adapters and passed here as bytes.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("helix.file_handler")

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Blacklisted extensions — large disk images and VM formats with no useful text content
BLACKLISTED_EXTENSIONS = {
    ".iso", ".dmg", ".vmdk", ".img", ".vhd", ".vdi",
    ".ova", ".ovf", ".qcow", ".qcow2", ".vbox",
}

UPLOAD_DIR = Path("/tmp/helix_uploads")


def validate_file(filename: str, size: int) -> Optional[str]:
    """Validate before downloading. Returns error string or None if valid."""
    if size > MAX_FILE_SIZE:
        size_mb = size / (1024 * 1024)
        return f"File too large ({size_mb:.1f}MB). Maximum is 5MB."
    ext = Path(filename).suffix.lower()
    if ext in BLACKLISTED_EXTENSIONS:
        return f"File type `{ext}` is not supported."
    return None


def save_file(filename: str, data: bytes) -> Optional[Path]:
    """Save bytes to the upload temp directory. Returns path or None on failure."""
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / filename
        dest.write_bytes(data)
        return dest
    except Exception as e:
        logger.error(f"Failed to save file {filename}: {e}")
        return None


def cleanup_file(path: Optional[Path]) -> None:
    """Remove a temp upload file after processing."""
    try:
        if path and path.exists():
            path.unlink()
    except Exception as e:
        logger.warning(f"Failed to clean up {path}: {e}")


def build_file_context(file_path: Path, user_message: str) -> str:
    """Inject the file path into the agent message."""
    if user_message.strip():
        return f"[Attached file: {file_path}]\n\n{user_message}"
    return f"[Attached file: {file_path}]\n\nThe user has attached a file. Please review it and let them know what you find."
