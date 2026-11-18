"""Start the web interface: python -m jobtracker.web"""
import os

import uvicorn

from ..config import load_env_file

if __name__ == "__main__":
    load_env_file()            # so JOBTRACKER_HOST and PORT can live in .env too
    uvicorn.run("jobtracker.web.app:create_app", factory=True,
                host=os.environ.get("JOBTRACKER_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", os.environ.get("JOBTRACKER_PORT", "8000"))))
