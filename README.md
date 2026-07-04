# ISY2NTFY PG3 node server

This project is ready to copy to PG3 and send ISY customization messages to ntfy.sh.

What it provides:

1. KEY custom parameter for ntfy token.
2. Message selection from ISY email/notification customization templates.
3. SEND command to publish the selected template to ntfy.sh.

## Important files

- `main.py`: PG3 controller node runtime.
- `isy2ntfy_node.py`: ISY customization fetch + ntfy publish logic.
- `server.json`: PG3 node server metadata.
- `install.sh`: PG3 install hook.
- `profile/`: Admin Console node definition, editors, and labels.

## PG3 custom parameters

Set these in PG3 Custom Configuration:

- `KEY`: ntfy bearer token (required)
- `TOPIC`: ntfy topic name (required)
- `ISY_URL`: ISY REST base URL (optional, defaults to `https://127.0.0.1`)

## Build a zip package on Windows

From this project folder:

```powershell
Compress-Archive -Path main.py,isy2ntfy_node.py,requirements.txt,install.sh,server.json,profile -DestinationPath isy2ntfy_pg3.zip -Force
```

## Install on PG3 (eisy/Polisy)

1. Open PG3 dashboard.
2. Install from local zip (or your local package workflow).
3. Select `isy2ntfy_pg3.zip`.
4. Add node server slot and start it.
5. Open Custom Configuration and set `KEY` and `TOPIC`.
6. Restart the node server once after saving params.

## Use in Admin Console

1. Open the ISY2NTFY controller node.
2. Set `Message Template` (GV0) from dropdown.
3. Run command `Refresh Templates` if you changed ISY customizations.
4. Run command `Send Selected Message`.

## Notes

- The message dropdown is generated from ISY customization templates at startup/refresh.
- If your ISY requires credentials for REST, set environment variables `ISY_USERNAME` and `ISY_PASSWORD` in your PG3 runtime environment.
- Version format is `yyyy.m.revision` in `server.json` profile_version.
- Current version is `2026.7.1` and revision can be 1 through 999.
