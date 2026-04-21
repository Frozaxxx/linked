from __future__ import annotations

import random

from config import (
    BLOCKED_RESOURCE_TYPES,
    PLAYWRIGHT_BROWSER_NAME,
    PLAYWRIGHT_HEADLESS,
    browser_ws_endpoint_candidates,
)


CHROME_VERSIONS = ("120.0.0.0", "119.0.0.0", "118.0.0.0", "117.0.0.0")
PLATFORMS = (
    "Windows NT 10.0; Win64; x64",
    "Macintosh; Intel Mac OS X 10_15_7",
    "X11; Linux x86_64",
)
SCREEN_RESOLUTIONS = (
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
)
ACCEPT_LANGUAGES = (
    "en-US,en;q=0.9",
    "ru-RU,ru;q=0.9,en;q=0.8",
)
STEALTH_INIT_SCRIPT = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5], configurable: true });
    Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'], configurable: true });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32', configurable: true });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
    window.chrome = { runtime: {}, loadTimes: function() { return {}; }, csi: function() { return {}; } };
    const originalQuery = navigator.permissions && navigator.permissions.query;
    if (originalQuery) {
        navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
    }
    if (window.WebGLRenderingContext) {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };
    }
}
"""


async def open_browser(playwright):
    browser_factory = getattr(playwright, PLAYWRIGHT_BROWSER_NAME)
    errors: list[str] = []
    for endpoint in browser_ws_endpoint_candidates():
        try:
            if "/playwright" in endpoint:
                return await browser_factory.connect(endpoint)
            return await browser_factory.connect_over_cdp(endpoint)
        except Exception as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    try:
        return await browser_factory.launch(headless=PLAYWRIGHT_HEADLESS)
    except Exception as exc:
        if errors:
            details = " | ".join(errors)
            raise RuntimeError(
                "Could not connect to Docker browserless and local Playwright launch failed. "
                f"Browserless attempts: {details}. Local launch: {type(exc).__name__}: {exc}"
            ) from exc
        raise

async def create_stealth_context(browser):
    viewport = random.choice(SCREEN_RESOLUTIONS)
    context = await browser.new_context(
        user_agent=generate_user_agent(),
        ignore_https_errors=True,
        viewport=viewport,
        screen=viewport,
        accept_downloads=False,
        java_script_enabled=True,
        is_mobile=False,
        has_touch=False,
        extra_http_headers=generate_browser_headers(),
    )
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return context

def generate_user_agent() -> str:
    platform = random.choice(PLATFORMS)
    chrome_version = random.choice(CHROME_VERSIONS)
    return f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"

def generate_browser_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Cache-Control": "no-cache",
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

async def block_heavy_resources(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    await route.continue_()

