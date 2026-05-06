import asyncio
import datetime as dt
from aiohttp import web

from .engine import AdvectEngine
from .base import SensorErrorType
from .logging import log


class StatusServer:
    def __init__(self, engine: AdvectEngine, port: int = 8080):
        self.engine = engine
        self.port = port
        self.runner = None

    async def health(self, request):
        return web.json_response({
            "status": "healthy",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "active_sensors": len(self.engine.sensors)
        })

    async def status(self, request):
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
                "healthy": sensor.healthy,
                "error_type": sensor.last_error_type.value,
                "error_message": sensor.last_error,
                "consecutive_errors": sensor.consecutive_errors
            })

        return web.json_response({
            "status": "running",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "active_sensors": len(self.engine.sensors),
            "sensors": sensors_status,
            "writer_queue_size": getattr(self.engine.writer, 'queue', None).qsize() 
                               if hasattr(self.engine.writer, 'queue') else 0,
        })

    async def html_status(self, request):
        """Dark mode dashboard"""
        now = asyncio.get_running_loop().time()
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>Advect-DAQ • Status</title>
            <meta http-equiv="refresh" content="12">
            <style>
                :root {{
                    --bg: #0f1117;
                    --card: #1a1f2e;
                    --text: #e0e0e0;
                    --text-muted: #a0a0a0;
                    --border: #2a3347;
                }}
                body {{ 
                    font-family: 'Segoe UI', Arial, sans-serif; 
                    margin: 0; 
                    padding: 20px; 
                    background: var(--bg); 
                    color: var(--text); 
                }}
                h1 {{ color: #4fc3f7; }}
                .header {{ margin-bottom: 20px; }}
                table {{ 
                    border-collapse: collapse; 
                    width: 100%; 
                    background: var(--card); 
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                }}
                th, td {{ 
                    padding: 14px; 
                    text-align: left; 
                    border-bottom: 1px solid var(--border);
                }}
                th {{ 
                    background: #1f2937; 
                    color: #90caf9;
                }}
                tr:hover {{ background: #252d3f; }}
                .ok {{ color: #66ff99; font-weight: bold; }}
                .warning {{ color: #ffcc33; font-weight: bold; }}
                .error {{ color: #ff6666; font-weight: bold; }}
                .error-msg {{ 
                    background: #2a1f1f; 
                    padding: 12px; 
                    border-left: 5px solid #ff6666; 
                    font-family: monospace;
                    white-space: pre-wrap;
                }}
                .expandable {{ cursor: pointer; }}
                .refresh {{ color: var(--text-muted); font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Advect-DAQ Status</h1>
                <p class="refresh">Last updated: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} UTC</p>
                <p><strong>Active Sensors:</strong> {len(self.engine.sensors)}</p>
            </div>
            
            <table>
                <tr>
                    <th>Sensor</th>
                    <th>Type</th>
                    <th>Interval</th>
                    <th>Last Read</th>
                    <th>Status</th>
                    <th>Info</th>
                </tr>
        """

        for name, sensor in self.engine.sensors.items():
            last = self.engine.last_success.get(name, 0)
            age = now - last if last > 0 else None
            error_type = sensor.last_error_type

            if sensor.healthy and age is not None and age < sensor.interval * 4:
                status_class = "ok"
                status_text = "OK"
            elif error_type == SensorErrorType.DATA_QUALITY:
                status_class = "warning"
                status_text = "WARNING"
            else:
                status_class = "error"
                status_text = "ERROR"

            age_str = f"{round(age, 1)}s ago" if age is not None else "Never"

            html += f"""
                <tr class="expandable" onclick="toggleError('{name}')">
                    <td><strong>{name}</strong></td>
                    <td>{getattr(getattr(sensor, 'config', None), 'type', 'unknown')}</td>
                    <td>{sensor.interval}s</td>
                    <td>{age_str}</td>
                    <td class="{status_class}">{status_text}</td>
                    <td>▼</td>
                </tr>
                <tr id="error-{name}" style="display: none;">
                    <td colspan="6">
                        <div class="error-msg">
                            <strong>Error Type:</strong> {error_type.value}<br>
                            <strong>Consecutive Errors:</strong> {sensor.consecutive_errors}<br>
                            <strong>Message:</strong> {sensor.last_error or 'No error'}
                        </div>
                    </td>
                </tr>
            """

        html += """
            </table>

            <p style="margin-top: 30px;">
                <a href="/status" style="color: #90caf9;">View JSON Status</a> | 
                <a href="/health" style="color: #90caf9;">Health Check</a>
            </p>

            <script>
                function toggleError(name) {
                    const row = document.getElementById('error-' + name);
                    row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
                }
            </script>
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
        log.success(f"🌐 Status server running on http://0.0.0.0:{self.port}")
        log.info(f"→ Dashboard: http://localhost:{self.port}/")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()