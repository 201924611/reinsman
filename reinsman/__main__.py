"""Entry point: `python -m reinsman` (or the `reinsman` console script) starts the FastAPI server."""
import uvicorn

from reinsman import config
from reinsman.runtime.server import app


def main() -> None:
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
