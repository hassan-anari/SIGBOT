import asyncio

from chainura.environment import load_project_environment
from chainura.telegram.runtime import run


if __name__ == "__main__":
    load_project_environment()
    asyncio.run(run())
