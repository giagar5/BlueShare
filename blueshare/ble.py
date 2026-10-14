"""HID-over-GATT peripheral on the Windows Bluetooth stack (WinRT), driven from any thread."""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from typing import Callable

from winrt.windows.devices.bluetooth import BluetoothError
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCharacteristicProperties as Props,
    GattLocalCharacteristicParameters,
    GattLocalDescriptorParameters,
    GattProtectionLevel,
    GattServiceProvider,
    GattServiceProviderAdvertisingParameters,
    GattWriteOption,
)
from winrt.windows.storage.streams import DataReader, DataWriter

from . import hid

log = logging.getLogger(__name__)

HID_SERVICE = 0x1812
REPORT_REFERENCE = 0x2908
INPUT_REPORT, OUTPUT_REPORT = 1, 2


def _uuid16(short: int) -> uuid.UUID:
    return uuid.UUID(f"0000{short:04x}-0000-1000-8000-00805f9b34fb")


def _to_buffer(data: bytes):
    writer = DataWriter()
    writer.write_bytes(data)
    return writer.detach_buffer()


def _from_buffer(buf) -> bytes:
    out = bytearray(buf.length)
    DataReader.from_buffer(buf).read_bytes(out)
    return bytes(out)


def _client_id(client) -> str:
    return client.session.device_id.id


class BleHidDevice:
    """Publishes keyboard, mouse and system-control input reports.

    Input reports are only notified to the trusted host (trust on first use), so no other
    device that happens to connect can receive keystrokes.
    """

    def __init__(self, trusted_host_id: str = "",
                 on_trusted_host: Callable[[str], None] | None = None,
                 on_connection_change: Callable[[bool], None] | None = None) -> None:
        self.trusted_host_id = trusted_host_id
        self.on_trusted_host = on_trusted_host
        self.on_connection_change = on_connection_change
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="blueshare-ble", daemon=True)
        self._provider = None
        self._chars: dict[str, object] = {}
        self._values: dict[str, bytes] = {}
        self._tokens: list = []
        self._queue: deque[list] = deque()
        self._queue_lock = threading.Lock()
        self._wake = asyncio.Event()
        self._buttons = 0
        self._connected = False
        self._sender: asyncio.Future | None = None

    # ---- lifecycle -------------------------------------------------------------------------

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._publish(), self._loop).result(timeout)
        self._sender = asyncio.run_coroutine_threadsafe(self._send_loop(), self._loop)

    def stop(self) -> None:
        async def shutdown():
            if self._provider is not None and self._provider.advertisement_status.name == "STARTED":
                self._provider.stop_advertising()
            if self._sender:
                self._sender.cancel()
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self._loop).result(5)
        except Exception:
            log.exception("BLE shutdown failed")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    @property
    def connected(self) -> bool:
        return self._connected

    def forget_host(self) -> None:
        def forget():
            self.trusted_host_id = ""
            self._update_connection()
        self._loop.call_soon_threadsafe(forget)

    # ---- thread-safe report API --------------------------------------------------------------

    def send_keyboard(self, report: bytes) -> None:
        self._enqueue(["kbd", report])

    def send_system(self, report: bytes) -> None:
        self._enqueue(["sys", report])

    def move(self, dx: int, dy: int, wheel: int = 0, hwheel: int = 0) -> None:
        """Relative motion; consecutive moves are merged while the BLE link is busy."""
        if not (dx or dy or wheel or hwheel) or not self._connected:
            return
        with self._queue_lock:
            last = self._queue[-1] if self._queue else None
            if last and last[0] == "mouse" and not last[6] and last[1] == self._buttons:
                last[2] += dx
                last[3] += dy
                last[4] += wheel
                last[5] += hwheel
            else:
                self._queue.append(["mouse", self._buttons, dx, dy, wheel, hwheel, False, 0.0])
        self._kick()

    def move_exact(self, dx: int, dy: int, settle: float = 0.0, buttons: int | None = None) -> None:
        """A move that is never merged with others, optionally followed by a pause.

        ``buttons`` overrides the held buttons for this report only (used for scripted drags).
        """
        self._enqueue(["mouse", self._buttons if buttons is None else buttons, dx, dy, 0, 0, True, settle])

    def pause(self, seconds: float) -> None:
        self._enqueue(["pause", seconds])

    def set_buttons(self, buttons: int) -> None:
        if buttons == self._buttons:
            return
        self._buttons = buttons
        self._enqueue(["mouse", buttons, 0, 0, 0, 0, True, 0.0])

    def release_all(self) -> None:
        self._buttons = 0
        self._enqueue(["mouse", 0, 0, 0, 0, 0, True, 0.0])
        self._enqueue(["kbd", hid.KeyboardState().report()])

    def _enqueue(self, item: list) -> None:
        if not self._connected:
            return
        with self._queue_lock:
            self._queue.append(item)
        self._kick()

    def _kick(self) -> None:
        self._loop.call_soon_threadsafe(self._wake.set)

    async def _send_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                with self._queue_lock:
                    if not self._queue:
                        break
                    item = self._queue.popleft()
                if item[0] == "kbd":
                    await self._notify("keyboard_input", item[1])
                elif item[0] == "sys":
                    await self._notify("system_input", item[1])
                elif item[0] == "pause":
                    await asyncio.sleep(item[1])
                else:
                    await self._send_mouse(*item[1:6])
                    if item[7]:
                        await asyncio.sleep(item[7])

    async def _send_mouse(self, buttons: int, dx: int, dy: int, wheel: int, hwheel: int) -> None:
        while True:
            cx = max(-hid.AXIS_LIMIT, min(hid.AXIS_LIMIT, dx))
            cy = max(-hid.AXIS_LIMIT, min(hid.AXIS_LIMIT, dy))
            cw = max(-hid.WHEEL_LIMIT, min(hid.WHEEL_LIMIT, wheel))
            ch = max(-hid.WHEEL_LIMIT, min(hid.WHEEL_LIMIT, hwheel))
            await self._notify("mouse_input", hid.mouse_report(buttons, cx, cy, cw, ch))
            dx, dy, wheel, hwheel = dx - cx, dy - cy, wheel - cw, hwheel - ch
            if not (dx or dy or wheel or hwheel):
                return

    async def _notify(self, name: str, data: bytes) -> None:
        self._values[name] = data
        client = self._trusted_subscriber(self._chars[name])
        if client is None:
            return
        try:
            await self._chars[name].notify_value_for_subscribed_client_async(_to_buffer(data), client)
        except Exception:
            log.exception("notify %s failed", name)

    # ---- connection tracking -----------------------------------------------------------------

    def _trusted_subscriber(self, char):
        if not self.trusted_host_id:
            return None
        for client in char.subscribed_clients:
            if _client_id(client) == self.trusted_host_id:
                return client
        return None

    def _on_subscriptions_changed(self, name: str, char) -> None:
        ids = [_client_id(c) for c in char.subscribed_clients]
        log.info("%s subscribers: %s", name, ids or "none")
        if not self.trusted_host_id and ids:
            self.trusted_host_id = ids[0]
            log.info("Trusting host %s", self.trusted_host_id)
            if self.on_trusted_host:
                self.on_trusted_host(self.trusted_host_id)
        for other in ids:
            if other != self.trusted_host_id:
                log.warning("Ignoring untrusted host %s", other)
        self._update_connection()

    def _update_connection(self) -> None:
        mouse = self._chars.get("mouse_input")
        connected = mouse is not None and self._trusted_subscriber(mouse) is not None
        if connected == self._connected:
            return
        self._connected = connected
        if not connected:
            with self._queue_lock:
                self._queue.clear()
        log.info("Tablet %s", "connected" if connected else "disconnected")
        if self.on_connection_change:
            self.on_connection_change(connected)

    # ---- GATT service ------------------------------------------------------------------------

    async def _publish(self) -> None:
        result = await GattServiceProvider.create_async(_uuid16(HID_SERVICE))
        if result.error != BluetoothError.SUCCESS:
            raise RuntimeError(f"Cannot create the HID service: {BluetoothError(result.error).name}")
        self._provider = result.service_provider
        svc = self._provider.service
        plain, encrypted = GattProtectionLevel.PLAIN, GattProtectionLevel.ENCRYPTION_REQUIRED

        await self._add(svc, "hid_information", 0x2A4A, Props.READ, bytes([0x11, 0x01, 0x00, 0x02]), plain, static=True)
        await self._add(svc, "report_map", 0x2A4B, Props.READ, hid.REPORT_MAP, encrypted, static=True)
        await self._add(svc, "hid_control_point", 0x2A4C, Props.WRITE_WITHOUT_RESPONSE, b"\x00", encrypted)
        await self._add(svc, "protocol_mode", 0x2A4E, Props.READ | Props.WRITE_WITHOUT_RESPONSE, b"\x01", encrypted)
        await self._add(svc, "keyboard_input", 0x2A4D, Props.READ | Props.NOTIFY, hid.KeyboardState().report(),
                        encrypted, report_ref=(hid.KEYBOARD_REPORT_ID, INPUT_REPORT))
        await self._add(svc, "keyboard_output", 0x2A4D, Props.READ | Props.WRITE | Props.WRITE_WITHOUT_RESPONSE,
                        b"\x00", encrypted, report_ref=(hid.KEYBOARD_REPORT_ID, OUTPUT_REPORT))
        await self._add(svc, "mouse_input", 0x2A4D, Props.READ | Props.NOTIFY, hid.mouse_report(0),
                        encrypted, report_ref=(hid.MOUSE_REPORT_ID, INPUT_REPORT))
        await self._add(svc, "system_input", 0x2A4D, Props.READ | Props.NOTIFY, hid.system_report(),
                        encrypted, report_ref=(hid.SYSTEM_REPORT_ID, INPUT_REPORT))

        self._tokens.append(self._provider.add_advertisement_status_changed(
            lambda s, a: log.info("Advertising: %s (%s)", a.status.name, BluetoothError(a.error).name)))
        params = GattServiceProviderAdvertisingParameters()
        params.is_connectable = True
        params.is_discoverable = True
        self._provider.start_advertising_with_parameters(params)

    async def _add(self, service, name, short_uuid, props, value, protection, static=False, report_ref=None):
        params = GattLocalCharacteristicParameters()
        params.characteristic_properties = props
        params.read_protection_level = protection
        params.write_protection_level = protection
        if static:
            params.static_value = _to_buffer(value)
        result = await service.create_characteristic_async(_uuid16(short_uuid), params)
        if result.error != BluetoothError.SUCCESS:
            raise RuntimeError(f"Cannot create {name}: {BluetoothError(result.error).name}")
        char = result.characteristic
        self._chars[name] = char
        self._values[name] = value
        if not static and props & Props.READ:
            self._tokens.append(char.add_read_requested(lambda s, a, n=name: self._on_read(n, a)))
        if props & (Props.WRITE | Props.WRITE_WITHOUT_RESPONSE):
            self._tokens.append(char.add_write_requested(lambda s, a, n=name: self._on_write(n, a)))
        if props & Props.NOTIFY:
            self._tokens.append(char.add_subscribed_clients_changed(
                lambda s, a, n=name: self._loop.call_soon_threadsafe(self._on_subscriptions_changed, n, s)))
        if report_ref is not None:
            dparams = GattLocalDescriptorParameters()
            dparams.read_protection_level = protection
            dparams.static_value = _to_buffer(bytes(report_ref))
            dres = await char.create_descriptor_async(_uuid16(REPORT_REFERENCE), dparams)
            if dres.error != BluetoothError.SUCCESS:
                raise RuntimeError(f"Cannot create report reference for {name}: {BluetoothError(dres.error).name}")

    def _on_read(self, name: str, args) -> None:
        deferral = args.get_deferral()

        async def respond():
            try:
                request = await args.get_request_async()
                trusted = _client_id(args) == self.trusted_host_id if self.trusted_host_id else False
                value = self._values[name] if trusted or not name.endswith("_input") else bytes(len(self._values[name]))
                request.respond_with_value(_to_buffer(value))
            except Exception:
                log.exception("read %s failed", name)
            finally:
                deferral.complete()

        asyncio.run_coroutine_threadsafe(respond(), self._loop)

    def _on_write(self, name: str, args) -> None:
        deferral = args.get_deferral()

        async def respond():
            try:
                request = await args.get_request_async()
                value = _from_buffer(request.value)
                if value != self._values.get(name):
                    log.info("Host wrote %s: %s", name, value.hex())
                self._values[name] = value
                if request.option == GattWriteOption.WRITE_WITH_RESPONSE:
                    request.respond()
            except Exception:
                log.exception("write %s failed", name)
            finally:
                deferral.complete()

        asyncio.run_coroutine_threadsafe(respond(), self._loop)
