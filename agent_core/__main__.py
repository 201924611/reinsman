"""Entry point: `python -m agent_core` starts the FastAPI server."""
import uvicorn

from agent_core import config
from agent_core.runtime.server import app

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
