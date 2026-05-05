import asyncio
import datetime as dt
from aiohttp import web

from .engine import AdvectEngine
from .logging import log


class StatusServer:
    def __init__(self, engine: AdvectEngine, port: int = 8081):
        self.engine = engine
        self.port = port
        self.runner = None

    async def health(self, request):
        """Simple health check"""
        return web.json_response({
            "status": "healthy",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "active_sensors": len(self.engine.sensors)
        })

    async def status(self, request):
        """Detailed JSON status"""
        now = asyncio.get_running_loop().time()
        sensors_status = []

        for name, sensor in self.engine.sensors.items():
            last = self.engine.last_success.get(name, 0)
            age = now - last if last > 0 else None

            sensor_type = getattr(getattr(sensor, 'config', None), 'type', 'unknown')

            sensors_status.append({
                "name": name,
                "type": sensor_type,
                "interval": sensor.interval,
                "last_read_seconds_ago": round(age, 1) if age is not None else None,
                "status": "ok" if age is not None and age < sensor.interval * 5 else "stale"
            })

        return web.json_response({
            "status": "running",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "active_sensors": len(self.engine.sensors),
            "sensors": sensors_status,
            "writer_queue_size": self.engine.writer.queue.qsize() if hasattr(self.engine.writer, 'queue') else 0,
        })

    async def html_status(self, request):
        """Simple nice HTML dashboard"""
        now = asyncio.get_running_loop().time()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Advect-DAQ Status</title>
            <meta http-equiv="refresh" content="10">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; background: #f8f9fa; }}
                h1 {{ color: #2c3e50; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ padding: 10px; border: 1px solid #ddd; text-align: left; }}
                th {{ background-color: #34495e; color: white; }}
                .ok {{ color: green; font-weight: bold; }}
                .stale {{ color: orange; font-weight: bold; }}
                .refresh {{ color: #666; font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <h1>Advect-DAQ Status</h1>
            <p class="refresh">Last updated: {dt.datetime.now(dt.timezone.utc).isoformat()}</p>
            
            <h2>System Overview</h2>
            <p><strong>Active Sensors:</strong> {len(self.engine.sensors)}</p>
            
            <h2>Sensors</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Interval (s)</th>
                    <th>Last Read</th>
                    <th>Status</th>
                </tr>
        """

        for name, sensor in self.engine.sensors.items():
            last = self.engine.last_success.get(name, 0)
            age = now - last if last > 0 else None
            status = "ok" if age is not None and age < sensor.interval * 5 else "stale"
            sensor_type = getattr(getattr(sensor, 'config', None), 'type', 'unknown')
            
            html += f"""
                <tr>
                    <td><strong>{name}</strong></td>
                    <td>{sensor_type}</td>
                    <td>{sensor.interval}</td>
                    <td>{round(age, 1) if age is not None else 'Never'}s ago</td>
                    <td class="{status}">{status.upper()}</td>
                </tr>
            """

        html += """
            </table>
            <p><a href="/status">View JSON Status</a> | <a href="/health">Health Check</a></p>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')

    async def start(self):
        app = web.Application()
        app.router.add_get('/health', self.health)
        app.router.add_get('/status', self.status)
        app.router.add_get('/', self.html_status) 

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()

        self.runner = runner
        log.success(f"Status server running on http://0.0.0.0:{self.port}")
        log.info(f"→ Dashboard: http://localhost:{self.port}/")
        log.info(f"→ JSON Status: http://localhost:{self.port}/status")
        log.info(f"→ Health: http://localhost:{self.port}/health")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()