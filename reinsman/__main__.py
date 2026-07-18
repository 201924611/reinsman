"""Entry point: `python -m reinsman` starts the FastAPI server."""
import uvicorn

from reinsman import config
from reinsman.runtime.server import app

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
