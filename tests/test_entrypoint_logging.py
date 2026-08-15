import logging

from app import __main__ as entrypoint


def test_configure_logging_enables_app_info_messages():
    assert hasattr(entrypoint, "configure_logging")
    app_logger = logging.getLogger("app")
    previous_level = app_logger.level
    try:
        app_logger.setLevel(logging.WARNING)

        entrypoint.configure_logging()

        assert app_logger.isEnabledFor(logging.INFO)
    finally:
        app_logger.setLevel(previous_level)
