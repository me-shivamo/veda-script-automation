"""Start the web interface: python -m web [--port 8000]"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m web")
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to let other machines on your network connect.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Open http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
    uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
