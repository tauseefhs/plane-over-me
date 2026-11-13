# Raspberry Pi setup

**Status: in progress.** The software runs today in the browser simulator. The LED panel driver and a one-command installer are being built and tested on real hardware. This page is the plan, and will become the step-by-step guide.

## The goal

1. Flash the SD card with Raspberry Pi Imager (point and click).
2. Paste one command on the Pi.
3. Plug in the panel. Planes appear, and it starts by itself every time the Pi is switched on.

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 3 Model B or Pi 4 | Built-in Wi-Fi (2.4 GHz on the Pi 3) |
| 64×32 RGB LED matrix, P4, HUB75 | 1/16 scan, no E line needed |
| Adafruit RGB Matrix Bonnet (3211) | Sits on the Pi's GPIO pins, powers the Pi and the panel from one 5 V input |
| 5 V 4 A power supply | Into the bonnet's barrel jack, not the Pi's USB port |
| microSD card, 16 GB or more, A1 or A2 rated | |
| BH1750 light sensor (optional) | I2C on GPIO2 (SDA) and GPIO3 (SCL), which the panel leaves free |

Once the bonnet is on, the Pi's usual case won't fit. The Pi sits behind the panel, inside a frame or box.

## Steps (draft)

1. **Flash the card.** In Raspberry Pi Imager choose Raspberry Pi OS Lite (64-bit). In its settings, set the hostname to `planeoverme`, a username and password, your Wi-Fi, and turn on SSH.
2. **First boot.** Connect with `ssh <user>@planeoverme.local`. Check the power supply with `vcgencmd get_throttled`: `0x0` means all good.
3. **Install the panel library**, [hzeller/rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix), with its Python bindings.
4. **Panel settings:** `--led-gpio-mapping=adafruit-hat`, `--led-rows=32`, `--led-cols=64`. Turn off the Pi's sound (`dtparam=audio=off` in `/boot/firmware/config.txt`), which disturbs the panel's timing.
5. **Light sensor:** turn on I2C with `raspi-config`.
6. **Start on boot:** run the app as a systemd service that restarts if anything goes wrong.

Steps 3 to 6 will be done by the installer.
