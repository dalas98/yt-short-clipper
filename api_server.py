from __future__ import annotations

import argparse
import os

from api.http_server import serve_api


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the YT Short Clipper HTTP API server")
    parser.add_argument("--host", default=os.environ.get("YTSC_API_HOST", "127.0.0.1"), help="Host interface to bind")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or os.environ.get("YTSC_API_PORT") or 8787),
        help="Port to listen on",
    )
    args = parser.parse_args()
    serve_api(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
