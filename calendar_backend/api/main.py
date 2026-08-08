"""Uvicorn entrypoint for calendar_backend API."""

from __future__ import annotations

import uvicorn
from calendar_backend.api.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, factory=False)


if __name__ == "__main__":
    main()
