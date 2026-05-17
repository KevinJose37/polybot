"""
HTTP health endpoint for liveness and readiness probes.
"""
import asyncio
import json
import structlog
from aiohttp import web

from bot.utils.clocks import current_timestamp_ms

logger = structlog.get_logger(__name__)


class HealthServer:
    """
    Lightweight async HTTP server exposing /health for monitoring.
    """
    def __init__(
        self,
        port: int = 8080,
        ws_connected_fn=None,
        books_fn=None,
        kill_switch_fn=None,
        stats_fn=None,
    ):
        self.port = port
        self._ws_connected_fn = ws_connected_fn or (lambda: False)
        self._books_fn = books_fn or (lambda: (0, 0))
        self._kill_switch_fn = kill_switch_fn or (lambda: False)
        self._stats_fn = stats_fn or (lambda: None)
        self._start_time_ms = current_timestamp_ms()

    async def _handle_health(self, request: web.Request) -> web.Response:
        """GET /health handler."""
        ws_ok = self._ws_connected_fn()
        active, stale = self._books_fn()
        kill = self._kill_switch_fn()
        stats = self._stats_fn()

        if kill:
            status = "down"
        elif not ws_ok or stale > 0:
            status = "degraded"
        else:
            status = "ok"

        body = {
            "status": status,
            "uptime_s": round((current_timestamp_ms() - self._start_time_ms) / 1000, 1),
            "ws_connected": ws_ok,
            "books_active": active,
            "books_stale": stale,
            "kill_switch": kill,
            "last_fill_ts": stats.trades[-1].timestamp if stats and stats.trades else None,
        }
        return web.json_response(body)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """
        GET /metrics handler.
        Note: net_pnl reports total PnL including unsettled TYPE-B positions,
        as we don't pass active_market_ids here.
        """
        stats = self._stats_fn()
        lines = []
        lines.append(f'polybot_fills_total {stats.fills_count if stats else 0}')
        lines.append(f'polybot_opportunities_detected {stats.opportunities_detected if stats else 0}')
        lines.append(f'polybot_opportunities_executed {stats.opportunities_executed if stats else 0}')
        lines.append(f'polybot_risk_rejections {stats.opportunities_rejected_risk if stats else 0}')
        lines.append(f'polybot_volume_usd {stats.total_volume if stats else 0}')
        lines.append(f'polybot_fees_usd {stats.total_fees_paid if stats else 0}')
        lines.append(f'polybot_pnl_net {stats.get_net_pnl(set()) if stats else 0}')
        lines.append(f'polybot_ws_connected {1 if self._ws_connected_fn() else 0}')
        lines.append(f'polybot_kill_switch_active {1 if self._kill_switch_fn() else 0}')
        active, stale = self._books_fn()
        lines.append(f'polybot_books_active {active}')
        lines.append(f'polybot_books_stale {stale}')
        return web.Response(text='\n'.join(lines) + '\n', content_type='text/plain')

    async def start(self) -> None:
        """Start the health HTTP server as a background task."""
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/metrics", self._handle_metrics)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        try:
            await site.start()
            logger.info("health_server_started", port=self.port)
            # Keep running
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await runner.cleanup()
        except OSError as e:
            logger.warning("health_server_bind_failed", port=self.port, error=str(e))
