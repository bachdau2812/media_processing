import logging

import uvicorn

from app.config import Settings
from app.main import create_app, create_uninitialized_services


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("app").setLevel(logging.INFO)


def main() -> None:
    configure_logging()
    settings = Settings()
    uvicorn.run(
        create_app(settings, create_uninitialized_services()),
        host=settings.service_host,
        port=settings.service_port,
        workers=1,
    )


if __name__ == "__main__":
    main()
