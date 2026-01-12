from aiohttp import web
import asyncio
import os

async def health_check(request):
    """Health check endpoint for Render."""
    return web.Response(text="Bot is running")

async def start_web_server():
    """Start simple web server for Render health checks."""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
    await site.start()
    # logger.info("Web server started on port 8080")


# Start web server for Render
asyncio.create_task(start_web_server())