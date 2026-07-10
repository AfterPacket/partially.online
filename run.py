import uvicorn
from backend.config import config

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host=config.HOST, port=config.PORT, reload=False)
