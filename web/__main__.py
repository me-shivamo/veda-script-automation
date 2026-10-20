"""Start the web interface: python -m web [--port 8000]"""

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m web")
    # A host such as Render tells the app where to listen through the HOST and PORT environment variables.
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Use 0.0.0.0 to let other machines connect.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    if args.host != "127.0.0.1" and not os.environ.get("APP_PASSWORD"):
        print("WARNING: other machines can reach this server and APP_PASSWORD is not set. Anyone who can open it can spend your Anthropic credit.")
    print(f"Open http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}", flush=True)
    uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
