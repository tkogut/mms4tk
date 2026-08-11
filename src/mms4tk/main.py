import argparse
import logging
import sys
import uvicorn

from mms4tk.api import app, daemon, executor, state_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("mms4tk.main")


def parse_args():
    parser = argparse.ArgumentParser(
        description="MMS4 Toolkit (mms4tk) Application Entrypoint & Async Daemon Server"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host address to bind FastAPI server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind FastAPI server (default: 8000)"
    )
    parser.add_argument(
        "--daemon-interval",
        type=float,
        default=15.0,
        help="Daemon market evaluation cycle interval in seconds (default: 15.0)"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=os.getenv("MMS4_SYMBOL", "BTC/USDC:USDC"),
        help="Market symbol for signal evaluation (default: env MMS4_SYMBOL or BTC/USDC:USDC)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info(f"Initializing MMS4 Toolkit Service on {args.host}:{args.port}...")
    logger.info(f"Configuring MMS4Daemon (symbol='{args.symbol}', interval={args.daemon_interval}s)...")

    daemon.interval_seconds = args.daemon_interval
    daemon.symbol = args.symbol

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
