# Windows hand check (OneDrive)

A one-time check, about 10 minutes, on a Windows 10 or 11 PC that is signed in to OneDrive (S0150; BL-0283, BL-0087). It saves to small test ledgers in a new OneDrive folder through Finzytrack's own engine and jobs, and prints a report to paste back into the session.

**What it touches.** Only two folders it makes itself:
- `Finzytrack hand check <date-time>` in your OneDrive folder (the test ledgers and `report.json`; it stays, so you can look at it; delete it when you are done);
- `ft-hand-check-…` in your temp folder (the app's own files for the check; removed at the end).

Installing writes `engine\.venv` and `backend\.venv` inside the folder you unzip to. Nothing else on the PC is changed, and your own ledgers and an installed Finzytrack are never opened.

## What you need
1. **uv** (it installs the right Python by itself; no administrator rights): in PowerShell run
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then open a new PowerShell window.
2. **This repository** at the commit you were given: on GitHub, switch to the branch `next`, then **Code → Download ZIP**. Unzip it to a folder **outside OneDrive**, for example `C:\ft-check`.
3. OneDrive running and signed in, with **Files On-Demand** on (the default).

## Run it
In PowerShell, in the unzipped folder:

```
powershell -ExecutionPolicy Bypass -File scripts\windows_hand_check\hand_check.ps1
```

If you installed a Finzytrack v0.4 test build (the packaged CI run's installer), add `-Installed "$env:LOCALAPPDATA\Finzytrack"` (or the folder you installed it to) to also report its engine's mode. The script only starts that build's engine to ask its mode; it opens no ledger with it. Your daily Finzytrack v0.3 is not touched either way.

The first part runs by itself (about 6 minutes: it waits for OneDrive's uploads). Then it asks you, one step at a time, and waits for Enter (type `s` and Enter to skip a step):
1. **Q1:** on onedrive.com, make a folder `web` with a text file in the check's folder (the script says exactly what to type), and wait until File Explorer shows `web` without opening it.
2. **Offline:** turn on airplane mode (or unplug the cable). The script opens and saves while offline. Then turn airplane mode off again.
3. **Paused:** pause OneDrive (its taskbar icon → Help & Settings → Pause syncing). The script opens once. Then resume syncing.

At the end it prints the report (JSON). Copy all of it into the session. It names files only relative to the check's folder; other paths are shown as `<home>`, `<onedrive>` and so on.

## What it checks
- the engine's placeholder mode as the app starts it (it must be 1);
- what OneDrive does after Finzytrack's save replaces a file (a plain file, a placeholder again, in sync);
- 8 saves one after another, so some meet OneDrive's upload of the previous one ("in use, try again" is a result, not a failure);
- cloud-only files ("Free up space") opened online: Windows should download them;
- offline and paused: what the open and a save say;
- a folder this PC has not listed yet, offline (Q1), and a ledger whose plugin file is cloud-only, offline (Q2).

The automatic part also runs in the backend's tests on every OS (`backend/tests/test_windows_hand_check.py`), so the script is known to work up to the steps that need OneDrive and your hands.
