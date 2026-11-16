
# audio_player.py
# Raspberry Pi Pico 2 W / RP2350 / MicroPython 1.28.0
#
# AudioPlayer supports:
#   1. RAW mono unsigned 8-bit PCM through PWM + DMA
#   2. A simple pulsed beep through PWM
#
# Hardware:
#   GP0 -> PAM8302 audio input
#   GP1 -> PAM8302 enable / shutdown (active HIGH)
#
# RAW format:
#   Headerless, mono, unsigned 8-bit PCM, 22050 Hz

import uasyncio as asyncio
import machine
import rp2

from array import array
from machine import Pin, PWM, mem32


class AudioPlayer:

    # RP2350 DMA timer 0
    DMA_TIMER0 = 0x50000440
    DMA_TIMER0_TREQ = 0x3B

    # PWM slice 0, channel A (GP0)
    PWM_CC = 0x400A800C
    PWM_TOP_REG = 0x400A8010

    def __init__(
        self,
        audio_pin=0,
        amp_enable_pin=1,
        default_file="lighthouse_sound.raw",
        sample_rate=22050,
        pwm_carrier_hz=20000,
        volume=0.55,
        block_samples=1024,
        amp_start_delay_ms=20,
    ):
        self.audio_pin = audio_pin
        self.amp_enable_pin = amp_enable_pin
        self.default_file = default_file
        self.sample_rate = sample_rate
        self.pwm_carrier_hz = pwm_carrier_hz
        self.volume = max(0.0, min(1.0, volume))
        self.block_samples = block_samples
        self.amp_start_delay_ms = amp_start_delay_ms

        self._amp_enable = None
        self._audio = None
        self._dma = None
        self._task = None
        self._state = "uninitialized"
        self._stop_requested = False
        self._last_error = None

    def init(self):
        if self._state != "uninitialized":
            return

        self._amp_enable = Pin(
            self.amp_enable_pin,
            Pin.OUT,
            value=0
        )

        self._audio = PWM(Pin(self.audio_pin))
        self._audio.freq(self.pwm_carrier_hz)
        self._audio.duty_u16(32768)

        self._state = "idle"
        self._last_error = None

    def is_playing(self):
        return self._state in ("starting", "playing", "stopping")

    def state(self):
        return self._state

    def last_error(self):
        return self._last_error

    def play(self, filename=None, loop=False):
        """Start RAW playback in the background."""
        self._check_ready()

        if self._state != "idle":
            raise RuntimeError("AudioPlayer is already playing")

        if filename is None:
            filename = self.default_file

        if filename is None:
            raise ValueError("No RAW filename specified")

        self._stop_requested = False
        self._last_error = None
        self._state = "starting"

        self._task = asyncio.create_task(
            self._run_raw(filename, loop)
        )

    def play_beep(
        self,
        frequency=1000,
        on_ms=300,
        off_ms=200,
        loop=True,
    ):
        """Start a pulsed tone in the background."""
        self._check_ready()

        if self._state != "idle":
            raise RuntimeError("AudioPlayer is already playing")

        if frequency <= 0:
            raise ValueError("Beep frequency must be positive")
        if on_ms <= 0 or off_ms < 0:
            raise ValueError("Invalid beep timing")

        self._stop_requested = False
        self._last_error = None
        self._state = "starting"

        self._task = asyncio.create_task(
            self._run_beep(frequency, on_ms, off_ms, loop)
        )

    def stop(self):
        """Request playback to stop; cleanup is done by the playback task."""
        if self.is_playing():
            self._stop_requested = True
            self._state = "stopping"

    async def deinit(self):
        """Stop playback and release PWM."""
        if self.is_playing():
            self.stop()

        if self._task is not None:
            while self._task is not None:
                await asyncio.sleep_ms(1)

        if self._audio is not None:
            self._audio.duty_u16(32768)
            self._audio.deinit()
            self._audio = None

        if self._amp_enable is not None:
            self._amp_enable.value(0)
            self._amp_enable = None

        self._state = "uninitialized"

    def _check_ready(self):
        if self._state == "uninitialized":
            raise RuntimeError("Call init() before playback")

    def _set_amp_off(self):
        if self._audio is not None:
            self._audio.duty_u16(32768)
        if self._amp_enable is not None:
            self._amp_enable.value(0)

    def _fill_buffer(self, buffer, file_obj, top, loop):
        """
        Fill one DMA buffer. If loop=True, continue from the start
        of the file when EOF is reached, including in the middle
        of a buffer.
        """
        filled = 0
        block_size = self.block_samples

        while filled < block_size and not self._stop_requested:
            raw = file_obj.read(block_size - filled)

            if not raw:
                if not loop:
                    break
                file_obj.seek(0)
                raw = file_obj.read(block_size - filled)
                if not raw:
                    raise RuntimeError("RAW audio file is empty")

            for sample_byte in raw:
                sample = sample_byte - 128
                duty_u16 = 32768 + int(sample * 256 * self.volume)

                if duty_u16 < 0:
                    duty_u16 = 0
                elif duty_u16 > 65535:
                    duty_u16 = 65535

                compare = (
                    duty_u16 * (top + 1) + 32767
                ) // 65535

                buffer[filled] = compare
                filled += 1

        return filled

    async def _wait_dma(self, dma):
        while dma.active():
            if self._stop_requested:
                return False
            await asyncio.sleep_ms(1)
        return True

    async def _run_raw(self, filename, loop):
        dma = None
        file_obj = None

        try:
            self._state = "starting"
            self._set_amp_off()

            # Restore RAW carrier if beep mode changed the PWM frequency.
            self._audio.freq(self.pwm_carrier_hz)
            self._audio.duty_u16(32768)
            top = mem32[self.PWM_TOP_REG] & 0xFFFF

            timer_x = round(machine.freq() / self.sample_rate)

            if timer_x > 65535:
                raise RuntimeError("DMA timer divider too large")
            if timer_x <= 0:
                raise RuntimeError("Invalid DMA timer divider")

            mem32[self.DMA_TIMER0] = (1 << 16) | timer_x

            file_obj = open(filename, "rb")

            buffer_a = array("H", [0] * self.block_samples)
            buffer_b = array("H", [0] * self.block_samples)

            count = self._fill_buffer(
                buffer_a, file_obj, top, loop
            )

            if count == 0:
                raise RuntimeError("No RAW samples found")

            dma = rp2.DMA()

            ctrl = dma.pack_ctrl(
                size=1,
                inc_read=True,
                inc_write=False,
                treq_sel=self.DMA_TIMER0_TREQ,
            )

            self._state = "playing"

            # Start the first DMA transfer.
            dma.config(
                read=buffer_a,
                write=self.PWM_CC,
                count=count,
                ctrl=ctrl,
                trigger=True,
            )

            # Let PWM run before enabling the amplifier.
            await asyncio.sleep_ms(self.amp_start_delay_ms)

            if self._stop_requested:
                return

            self._amp_enable.value(1)

            current_buffer = buffer_a
            next_buffer = buffer_b

            while not self._stop_requested:

                # Fill the next buffer while DMA transfers the current one.
                next_count = self._fill_buffer(
                    next_buffer,
                    file_obj,
                    top,
                    loop
                )

                if self._stop_requested:
                    break

                # Wait for the current transfer to finish.
                if not await self._wait_dma(dma):
                    break

                if next_count == 0:
                    # Non-looping file has reached EOF.
                    break

                dma.config(
                    read=next_buffer,
                    write=self.PWM_CC,
                    count=next_count,
                    ctrl=ctrl,
                    trigger=True,
                )

                current_buffer, next_buffer = (
                    next_buffer, current_buffer
                )

            # Do not leave a DMA transfer running during cleanup.
            if dma.active():
                dma.active(0)

        except Exception as exc:
            self._last_error = exc
            print("AudioPlayer RAW error:", exc)

        finally:
            self._set_amp_off()

            if dma is not None:
                try:
                    if dma.active():
                        dma.active(0)
                    dma.close()
                except Exception:
                    pass

            if file_obj is not None:
                try:
                    file_obj.close()
                except Exception:
                    pass

            self._dma = None
            self._task = None
            self._stop_requested = False

            if self._state != "uninitialized":
                self._state = "idle"

    async def _run_beep(self, frequency, on_ms, off_ms, loop):
        try:
            self._state = "starting"
            self._set_amp_off()

            self._audio.freq(frequency)
            self._audio.duty_u16(32768)

            # Allow PWM to stabilize before enabling the amplifier.
            await asyncio.sleep_ms(20)

            if self._stop_requested:
                return

            self._amp_enable.value(1)
            self._state = "playing"

            while not self._stop_requested:
                self._audio.duty_u16(32768)
                await asyncio.sleep_ms(on_ms)

                if self._stop_requested:
                    break

                self._audio.duty_u16(0)

                if off_ms:
                    await asyncio.sleep_ms(off_ms)

                if not loop:
                    break

        except Exception as exc:
            self._last_error = exc
            print("AudioPlayer beep error:", exc)

        finally:
            self._set_amp_off()
            self._task = None
            self._stop_requested = False

            if self._state != "uninitialized":
                self._state = "idle"
