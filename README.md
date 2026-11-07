# Black Box

A flight recorder for your Windows PC. It quietly records what your computer is doing, then tells you in plain English **why it was slow**, even for a lag that happened an hour ago.

Task Manager only shows what's happening right now. Black Box remembers.

## Download

1. Go to the [Releases](../../releases) page and download `BlackBox-windows.zip`.
2. Right-click the zip, choose **Extract All**, and open the extracted folder.
3. Double-click `BlackBox.exe`. Keep it inside its folder, because it needs the files next to it.

Windows SmartScreen may warn about unsigned apps: click **More info → Run anyway**. Some antivirus programs flag unsigned apps built with PyInstaller by mistake. The full source is in this repo, and the release includes a `.sha256` file so you can verify your download:

```
certutil -hashfile BlackBox-windows.zip SHA256
```

## What it does

- **Why is my PC slow right now?** One button. It looks at the last 2 minutes and names what is using your PC.
- **Timeline.** A colored strip of the last hour, 6 hours, 24 hours or 3 days. Red parts were overloaded. Click any part to see what happened then.
- **Slow moments.** A list of the times your CPU, memory or disk were overloaded, each with a one-line cause, like *"MsMpEng (Windows Defender scanning your files) was hammering the disk."*
- **Plain-English explanations.** Common Windows and app processes are described in normal words, with a tip for what helps.
- **Full history chart** for each moment, plus the busiest programs by CPU, memory and I/O.
- A warning when your system drive is nearly full, since that slows Windows down by itself.

## Privacy

Everything stays on your PC. Black Box makes no network connections and has no account or telemetry. It records only numbers and program names (no screenshots, no window titles, no file names), in `%APPDATA%\BlackBox`. You choose how long history is kept, and you can clear it any time in settings.

## Tips

- Recording only works while Black Box is running. **Minimize it instead of quitting**; the close button minimizes by default.
- Turn on **Start with Windows** in settings so it records from boot.
- Settings let you change how often it records (2 to 30 seconds), how long it keeps history (1 to 14 days), and what counts as overloaded.

## Build from source

```
pip install -r requirements.txt
python blackbox.py
```

To build the `.exe` yourself, run `build.bat`. Or let GitHub do it: push a tag like `v1.0.0` and the workflow in `.github/workflows/build.yml` builds the app and attaches it to a Release.

## License

MIT
