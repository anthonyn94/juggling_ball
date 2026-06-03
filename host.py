import asyncio
import time
from collections import deque
from bleak import BleakScanner, BleakClient
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.console import Console
import threading

BALL_PREFIX   = "ball_"
UART_TX_UUID  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
UART_RX_UUID  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

WINDOW        = 5.0
MAX_LOG_LINES = 100
RECONNECT_S   = 3.0

# ── shared state ──────────────────────────────────────────────────────────────
message_timestamps: dict[str, list[float]] = {}
log_lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
ball_status: dict[str, str] = {}
reconnect_tasks: dict[str, asyncio.Task] = {}
buffers: dict[str, str] = {}
active_clients: dict[str, BleakClient] = {}

# clock sync: host_time = ball_time + offset
# populated on first message from each ball, cleared on reconnect
CLOCK_SYNC_SAMPLES = 20
CLOCK_RESYNC_EVERY = 200  # recalculate offset every N messages

clock_sync_samples: dict[str, list[float]] = {}
clock_offsets: dict[str, float] = {}
clock_message_count: dict[str, int] = {}

# per-ball delivery delay stats for display (rolling average)
delivery_delays: dict[str, deque] = {}

console = Console()

# ── display ───────────────────────────────────────────────────────────────────

COMMAND_HELP = (
    "[dim]Commands:[/dim]  "
    "[cyan]COLOR <ball|ALL> <r> <g> <b>[/cyan]  "
    "[cyan]COLOR <ball|ALL> RESET[/cyan]  "
    "[cyan]MODE <ball|ALL> rainbow|blue[/cyan]"
)

def build_display() -> Layout:
    now    = time.monotonic()
    cutoff = now - WINDOW

    table = Table(box=box.SIMPLE, expand=True)
    table.add_column("Ball",         style="cyan", width=12)
    table.add_column("Status",       width=14)
    table.add_column("Rate (msg/s)", justify="right", width=14)
    table.add_column("Avg delay",    justify="right", width=12)
    table.add_column("Clock offset", justify="right", width=14)

    for name in sorted(ball_status.keys()):
        status = ball_status[name]
        if status == "connected":
            status_str = "[green]connected[/green]"
        elif status == "connecting":
            status_str = "[yellow]connecting…[/yellow]"
        else:
            status_str = "[red]disconnected[/red]"

        message_timestamps.setdefault(name, [])
        message_timestamps[name] = [t for t in message_timestamps[name] if t >= cutoff]
        rate = len(message_timestamps[name]) / WINDOW
        rate_str = f"[green]{rate:.1f}[/green]" if rate > 1 else f"[red]{rate:.1f}[/red]"

        # average delivery delay over recent messages
        delays = delivery_delays.get(name)
        if delays and len(delays) > 0:
            avg_delay = sum(delays) / len(delays)
            delay_str = f"{avg_delay*1000:.1f}ms"
        else:
            delay_str = "[dim]syncing…[/dim]"

        # clock offset
        offset = clock_offsets.get(name)
        offset_str = f"{offset:.3f}s" if offset is not None else "[dim]syncing…[/dim]"

        table.add_row(name, status_str, rate_str, delay_str, offset_str)

    rate_panel = Panel(table, title="Balls", border_style="bright_blue")
    log_panel  = Panel(
        "\n".join(log_lines) or "[dim]waiting for messages…[/dim]",
        title="Raw Messages",
        border_style="bright_blue",
    )
    help_panel = Panel(COMMAND_HELP, title="Send Command", border_style="yellow", height=3)

    layout = Layout()
    layout.split_column(
        Layout(rate_panel,  size=len(ball_status) + 6),
        Layout(log_panel),
        Layout(help_panel,  size=3),
    )
    return layout


# ── clock sync ────────────────────────────────────────────────────────────────

def sync_clock(name: str, ball_ts: float, host_arrival: float):
    sample = host_arrival - ball_ts
    count = clock_message_count.get(name, 0) + 1
    clock_message_count[name] = count

    samples = clock_sync_samples.setdefault(name, [])
    samples.append(sample)

    # keep only the most recent window of samples
    if len(samples) > CLOCK_SYNC_SAMPLES:
        samples.pop(0)

    # commit/update offset once we have enough samples,
    # then refresh every CLOCK_RESYNC_EVERY messages
    if len(samples) == CLOCK_SYNC_SAMPLES:
        if name not in clock_offsets or count % CLOCK_RESYNC_EVERY == 0:
            new_offset = min(samples)
            old_offset = clock_offsets.get(name)
            clock_offsets[name] = new_offset
            if old_offset is not None:
                drift = (new_offset - old_offset) * 1000
                log_lines.append(
                    f"[dim]{name}: clock resynced "
                    f"offset={new_offset:.3f}s "
                    f"drift={drift:+.1f}ms[/dim]"
                )
            else:
                jitter = (max(samples) - min(samples)) * 1000
                log_lines.append(
                    f"[dim]{name}: clock synced "
                    f"offset={new_offset:.3f}s "
                    f"jitter={jitter:.1f}ms[/dim]"
                )


def ball_to_host_time(name: str, ball_ts: float) -> float | None:
    """Convert a ball timestamp to host timebase. Returns None if not synced."""
    offset = clock_offsets.get(name)
    return ball_ts + offset if offset is not None else None


# ── BLE helpers ───────────────────────────────────────────────────────────────

def make_handler(name: str):
    def handler(_, data):
        host_arrival = time.monotonic()
        chunk = data.decode("utf-8")
        buffers[name] = buffers.get(name, "") + chunk

        while "\n" in buffers[name]:
            line, buffers[name] = buffers[name].split("\n", 1)
            line = line.strip()
            if not line:
                continue

            parts = line.split("|")
            verb = parts[0]

            # ── IMU message: I|<ball_ts>|ax|ay|az|gx|gy|gz
            if verb == "I" and len(parts) >= 2:
                try:
                    ball_ts = float(parts[1])
                    sync_clock(name, ball_ts, host_arrival)
                    event_time = ball_to_host_time(name, ball_ts)
                    if event_time is not None:
                        delay = host_arrival - event_time
                        delivery_delays.setdefault(name, deque(maxlen=50)).append(delay)
                        log_lines.append(
                            f"[cyan]{name}[/cyan] "
                            f"(delay={delay*1000:.1f}ms): {line}"
                        )
                    else:
                        log_lines.append(f"[cyan]{name}[/cyan]: {line}")
                except (ValueError, IndexError):
                    log_lines.append(f"[cyan]{name}[/cyan]: {line}")

            # ── TAP message: TAP|<device_id>|<ball_ts>|<magnitude>
            elif verb == "TAP" and len(parts) >= 3:
                try:
                    ball_ts = float(parts[2])
                    sync_clock(name, ball_ts, host_arrival)
                    event_time = ball_to_host_time(name, ball_ts)
                    if event_time is not None:
                        delay = host_arrival - event_time
                        delivery_delays.setdefault(name, deque(maxlen=50)).append(delay)
                        log_lines.append(
                            f"[bold yellow]{name} TAP[/bold yellow] "
                            f"(delay={delay*1000:.1f}ms "
                            f"ball_ts={ball_ts:.4f}): {line}"
                        )
                    else:
                        log_lines.append(f"[bold yellow]{name} TAP[/bold yellow]: {line}")
                except (ValueError, IndexError):
                    log_lines.append(f"[bold yellow]{name} TAP[/bold yellow]: {line}")

            else:
                log_lines.append(f"[cyan]{name}[/cyan]: {line}")

            message_timestamps.setdefault(name, []).append(host_arrival)

    return handler


def make_disconnect_callback(name: str, address: str):
    def callback(_):
        ball_status[name] = "disconnected"
        active_clients.pop(name, None)
        log_lines.append(f"[yellow]{name} disconnected — will retry[/yellow]")
        if name not in reconnect_tasks or reconnect_tasks[name].done():
            reconnect_tasks[name] = asyncio.ensure_future(reconnect_ball(name, address))
    return callback


async def connect_ball(name: str, address: str) -> BleakClient:
    ball_status[name] = "connecting"
    # clear stale sync state so we re-sync on first message after reconnect
    clock_offsets.pop(name, None)
    clock_sync_samples.pop(name, None)
    clock_message_count.pop(name, None)
    delivery_delays.pop(name, None)

    client = BleakClient(address, disconnected_callback=make_disconnect_callback(name, address))
    await client.connect()
    await client.start_notify(UART_TX_UUID, make_handler(name))
    ball_status[name] = "connected"
    active_clients[name] = client
    message_timestamps.setdefault(name, [])
    log_lines.append(f"[green]{name} connected ({address})[/green]")
    return client


async def reconnect_ball(name: str, address: str):
    while True:
        await asyncio.sleep(RECONNECT_S)
        try:
            await connect_ball(name, address)
            return
        except Exception as e:
            log_lines.append(f"[red]{name} reconnect failed: {e}[/red]")


# ── command sender ────────────────────────────────────────────────────────────

async def send_command(target: str, cmd: str):
    if not cmd.endswith("\n"):
        cmd += "\n"
    targets = list(active_clients.keys()) if target.upper() == "ALL" else [target]
    for name in targets:
        client = active_clients.get(name)
        if client is None or not client.is_connected:
            log_lines.append(f"[red]Cannot send to {name}: not connected[/red]")
            continue
        try:
            await client.write_gatt_char(UART_RX_UUID, cmd.encode())
            log_lines.append(f"[yellow]→ {name}:[/yellow] {cmd.strip()}")
        except Exception as e:
            log_lines.append(f"[red]Send to {name} failed: {e}[/red]")


def parse_and_queue(raw: str, loop: asyncio.AbstractEventLoop):
    parts = raw.strip().split()
    if not parts:
        return
    verb = parts[0].upper()

    if verb == "COLOR" and len(parts) == 5:
        _, target, r, g, b = parts
        asyncio.run_coroutine_threadsafe(send_command(target, f"COLOR|{r}|{g}|{b}"), loop)
    elif verb == "COLOR" and len(parts) == 3 and parts[2].upper() == "RESET":
        _, target, _ = parts
        asyncio.run_coroutine_threadsafe(send_command(target, "COLOR|RESET"), loop)
    elif verb == "MODE" and len(parts) == 3:
        _, target, mode = parts
        asyncio.run_coroutine_threadsafe(send_command(target, f"MODE|{mode}"), loop)
    else:
        console.print(
            f"[red]Unknown command:[/red] {raw}\n"
            "  COLOR <ball|ALL> <r> <g> <b>\n"
            "  COLOR <ball|ALL> RESET\n"
            "  MODE  <ball|ALL> rainbow|blue"
        )


def input_thread(loop: asyncio.AbstractEventLoop):
    while True:
        try:
            raw = input()
        except EOFError:
            break
        if raw.strip():
            parse_and_queue(raw, loop)


# ── scanner ───────────────────────────────────────────────────────────────────

async def scan_and_connect() -> list[BleakClient]:
    found_addresses: set[str] = set()
    clients: list[BleakClient] = []
    connect_lock = asyncio.Lock()

    def detection_callback(device, _adv_data):
        if not (device.name and device.name.startswith(BALL_PREFIX)):
            return
        if device.address in found_addresses:
            return
        found_addresses.add(device.address)
        name = device.name

        async def _connect():
            async with connect_lock:
                try:
                    client = await connect_ball(name, device.address)
                    clients.append(client)
                except Exception as e:
                    ball_status[name] = "disconnected"
                    log_lines.append(f"[red]Failed to connect to {name}: {e}[/red]")
                    reconnect_tasks[name] = asyncio.ensure_future(
                        reconnect_ball(name, device.address)
                    )

        asyncio.ensure_future(_connect())

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    log_lines.append("[dim]Scanner started — watching for ball_* devices…[/dim]")

    while not clients:
        await asyncio.sleep(0.5)

    asyncio.ensure_future(_keep_scanner_alive(scanner))
    return clients


async def _keep_scanner_alive(scanner: BleakScanner):
    while True:
        await asyncio.sleep(60)


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    loop = asyncio.get_running_loop()
    t = threading.Thread(target=input_thread, args=(loop,), daemon=True)
    t.start()

    clients = await scan_and_connect()

    with Live(build_display(), refresh_per_second=4, screen=False) as live:
        try:
            while True:
                await asyncio.sleep(0.25)
                live.update(build_display())
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for client in clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
