"""Run SpotiFLAC native providers without browser-backed community endpoints."""

from importlib.metadata import version

SUPPORTED_SPOTIFLAC_VERSION = "1.6.0"


def _without_url(urls, blocked_url):
    blocked = (blocked_url or "").rstrip("/")
    return [url for url in urls if not blocked or url.rstrip("/") != blocked]


def _apply_native_no_browser_policy():
    installed_version = version("spotiflac")
    if installed_version != SUPPORTED_SPOTIFLAC_VERSION:
        raise RuntimeError(
            "Unsupported SpotiFLAC version "
            f"{installed_version}; expected {SUPPORTED_SPOTIFLAC_VERSION}"
        )

    def browser_disabled(*_args, **_kwargs):
        raise RuntimeError("Browser verification is disabled for backend automation")

    async def browser_disabled_async(*_args, **_kwargs):
        raise RuntimeError("Browser verification is disabled for backend automation")

    import webbrowser
    from SpotiFLAC.core import signed_session_desktop, signed_session_mobile, solver

    webbrowser.open = browser_disabled
    webbrowser.open_new = browser_disabled
    webbrowser.open_new_tab = browser_disabled
    solver.solve_with_callback = browser_disabled
    signed_session_desktop.run_community_verification = browser_disabled
    signed_session_desktop.ensure_community_session = browser_disabled
    signed_session_mobile.SignedSessionClient.authenticate_with_turnstile = (
        browser_disabled_async
    )

    from SpotiFLAC.providers import qobuz, tidal

    qobuz._COMMUNITY_APIS.clear()

    community_url = tidal._TIDAL_COMMUNITY_URL
    tidal._TIDAL_API_POST = _without_url(tidal._TIDAL_API_POST, community_url)
    tidal._CLEAN_POST_APIS = frozenset(
        url.rstrip("/") for url in tidal._TIDAL_API_POST
    )

    original_get = tidal.get_tidal_api_list
    original_refresh = tidal.refresh_tidal_api_list_async

    def safe_get():
        return _without_url(original_get(), community_url)

    async def safe_refresh(force=False):
        urls = await original_refresh(force=force)
        return _without_url(urls, community_url)

    tidal.get_tidal_api_list = safe_get
    tidal.refresh_tidal_api_list_async = safe_refresh

    print(
        f"SpotiFLAC version={installed_version} "
        "browserPolicy=community-disabled",
        flush=True,
    )


_apply_native_no_browser_policy()

from SpotiFLAC.launcher import main  # noqa: E402

main()
