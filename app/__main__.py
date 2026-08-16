import uvicorn

from app.config import Settings
from app.logging_config import configure_logging
from app.main import create_app, create_uninitialized_services


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    uvicorn.run(
        create_app(settings, create_uninitialized_services()),
        host=settings.service_host,
        port=settings.service_port,
        workers=1,
        log_config=None,
    )


if __name__ == "__main__":
    main()
