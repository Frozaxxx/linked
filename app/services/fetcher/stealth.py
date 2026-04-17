from __future__ import annotations

import random
from dataclasses import dataclass

from app.settings import get_settings


settings = get_settings()

CHROME_VERSIONS: tuple[str, ...] = (
    "120.0.0.0",
    "119.0.0.0",
    "118.0.0.0",
    "117.0.0.0",
    "116.0.0.0",
    "115.0.0.0",
    "114.0.0.0",
)
PLATFORMS: tuple[str, ...] = (
    "Windows NT 10.0; Win64; x64",
    "Windows NT 6.1; Win64; x64",
    "Macintosh; Intel Mac OS X 10_15_7",
    "X11; Linux x86_64",
)
SCREEN_RESOLUTIONS: tuple[dict[str, int], ...] = (
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
)
LANGUAGES: tuple[str, ...] = (
    "en-US,en;q=0.9",
    "ru-RU,ru;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
)
LOCALES_BY_ACCEPT_LANGUAGE: dict[str, str] = {
    "en-US,en;q=0.9": "en-US",
    "ru-RU,ru;q=0.9,en;q=0.8": "ru-RU",
    "de-DE,de;q=0.9,en;q=0.8": "de-DE",
    "fr-FR,fr;q=0.9,en;q=0.8": "fr-FR",
}

FINGERPRINT_SPOOFING_SCRIPT = """
() => {
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
        configurable: true
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['%s', '%s'],
        configurable: true
    });
    Object.defineProperty(navigator, 'platform', {
        get: () => '%s',
        configurable: true
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => %d,
        configurable: true
    });
    const originalQuery = navigator.permissions.query;
    navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );
    window.chrome = {
        runtime: {},
        loadTimes: function() { return {}; },
        csi: function() { return {}; },
        src: { isInstalled: false }
    };
}
"""

WEBGL_SPOOFING_SCRIPT = """
() => {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Intel Inc.';
        }
        if (parameter === 37446) {
            return 'Intel Iris OpenGL Engine';
        }
        return getParameter.call(this, parameter);
    };
}
"""

CANVAS_SPOOFING_SCRIPT = """
() => {
    const toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const context = this.getContext('2d');
        if (context) {
            context.fillText('Modified Canvas Fingerprint', 10, 10);
        }
        return toDataURL.call(this, type);
    };
}
"""


@dataclass(frozen=True)
class BrowserFingerprint:
    user_agent: str
    viewport: dict[str, int]
    accept_language: str
    locale: str
    platform: str
    hardware_concurrency: int
    has_touch: bool


def build_browser_fingerprint() -> BrowserFingerprint:
    return _build_fingerprint()


def build_browser_context_options(fingerprint: BrowserFingerprint | None = None) -> dict:
    fingerprint = fingerprint or _build_fingerprint()
    options = {
        "user_agent": fingerprint.user_agent,
        "locale": fingerprint.locale,
        "viewport": fingerprint.viewport,
        "timezone_id": settings.fetch_browser_timezone_id,
        "extra_http_headers": _build_extra_http_headers(fingerprint),
    }
    if settings.fetch_browser_stealth_enabled:
        options.update(
            {
                "screen": fingerprint.viewport,
                "accept_downloads": False,
                "ignore_https_errors": settings.fetch_browser_ignore_https_errors,
                "java_script_enabled": True,
                "has_touch": fingerprint.has_touch,
                "is_mobile": False,
            }
        )
    return options


def build_init_scripts(fingerprint: BrowserFingerprint | None = None) -> tuple[str, ...]:
    if not settings.fetch_browser_stealth_enabled:
        return ()
    fingerprint = fingerprint or _build_fingerprint()
    language_parts = [part.split(";")[0] for part in fingerprint.accept_language.split(",")]
    primary_language = language_parts[0] if language_parts else "en-US"
    secondary_language = language_parts[1] if len(language_parts) > 1 else primary_language.split("-")[0]
    return (
        CANVAS_SPOOFING_SCRIPT,
        FINGERPRINT_SPOOFING_SCRIPT
        % (
            primary_language,
            secondary_language,
            fingerprint.platform,
            fingerprint.hardware_concurrency,
        ),
        WEBGL_SPOOFING_SCRIPT,
    )


def _build_fingerprint() -> BrowserFingerprint:
    if settings.fetch_browser_randomize_fingerprint:
        platform = random.choice(PLATFORMS)  # noqa: S311
        chrome_version = random.choice(CHROME_VERSIONS)  # noqa: S311
        viewport = dict(random.choice(SCREEN_RESOLUTIONS))  # noqa: S311
        accept_language = random.choice(LANGUAGES)  # noqa: S311
        hardware_concurrency = random.choice((4, 8, 12, 16))  # noqa: S311
        has_touch = random.choice((True, False))  # noqa: S311
        user_agent = (
            f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        )
        return BrowserFingerprint(
            user_agent=user_agent,
            viewport=viewport,
            accept_language=accept_language,
            locale=LOCALES_BY_ACCEPT_LANGUAGE.get(accept_language, "en-US"),
            platform=platform,
            hardware_concurrency=hardware_concurrency,
            has_touch=has_touch,
        )

    viewport = {
        "width": settings.fetch_browser_viewport_width,
        "height": settings.fetch_browser_viewport_height,
    }
    return BrowserFingerprint(
        user_agent=settings.fetch_user_agent,
        viewport=viewport,
        accept_language="en-US,en;q=0.9",
        locale="en-US",
        platform="Windows NT 10.0; Win64; x64",
        hardware_concurrency=8,
        has_touch=False,
    )


def _build_extra_http_headers(fingerprint: BrowserFingerprint) -> dict[str, str]:
    headers = {
        "Accept": settings.fetch_accept_header,
        "Accept-Language": fingerprint.accept_language,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if settings.fetch_browser_stealth_enabled:
        headers.update(
            {
                "DNT": str(random.randint(0, 1)),  # noqa: S311
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )
    return headers
