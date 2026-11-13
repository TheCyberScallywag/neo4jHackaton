"""Start one service: python -m app.serve [web|admin].

The Docker image runs this as its entrypoint (SERVICE comes from the environment). Plain Python,
so it runs the same on Windows, macOS and Linux.
"""

import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    service = args[0] if args else os.environ.get("SERVICE", "")
    host = os.environ.get("HOST", "127.0.0.1")
    logging.basicConfig(level=logging.INFO)
    if service == "web":
        import uvicorn

        uvicorn.run(
            "app.web.main:create_app",
            factory=True,
            host=host,
            port=int(os.environ.get("PORT", "8080")),
            proxy_headers=False,
        )
    elif service == "admin":
        from streamlit.web import cli

        import app.admin

        script = Path(app.admin.__file__).parent / "main.py"
        sys.argv = [
            "streamlit", "run", str(script),
            "--server.address", host, "--server.port", os.environ.get("ADMIN_PORT", "8501"),
            "--server.headless", "true", "--browser.gatherUsageStats", "false",
        ]  # fmt: skip
        sys.exit(cli.main())
    else:
        sys.exit(f"Unknown SERVICE '{service}' (expected web|admin)")


if __name__ == "__main__":
    main()
