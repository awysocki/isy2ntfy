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
- `NTFY_URL`: ntfy base URL (optional, defaults to `https://ntfy.sh`)
- `SEND_ID`: include `X-ID` on publish (optional, defaults to `true`)
- `ID_PREFIX`: prefix for generated message IDs (optional, defaults to `msg`)
- `ISY_REST_URL`: optional ISY REST URL for template-fetch mode (`SEND`/`Refresh Templates`)

Examples for `NTFY_URL`:

- `https://ntfy.sh`

The node server builds the final topic URL internally (no `/publish` required) and sends the live message body from PG3.
It sends different default ntfy tags/icons by message type:
- Startup announcements use `rocket`.
- ISY message sends (SEND/GV10) use `bell`.

KEY behavior:

- If KEY starts with `tk_`, KEY is treated as an access token (Bearer auth) and topic defaults to `isy2ntfy`.
- Otherwise KEY is treated as the topic name and no Authorization header is added.

Message ID behavior:

- If `SEND_ID=true`, each message includes `X-ID` header only (no `id` in URL).
- IDs use a persisted counter and survive restart (e.g. `msg001`, `msg002`, ...).
- Generated IDs are timestamp-based, like `msg1720101234567`.
- Set `SEND_ID=false` to omit IDs.
- GV10 payloads with source `@_id` reuse that ID when `SEND_ID=true`.

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
5. Open Custom Configuration and set `KEY` (plus optional `NTFY_URL`).
6. Restart the node server once after saving params.

## Use in Admin Console

1. Open the ISY2NTFY controller node.
2. Set `Message Template` (GV0) from dropdown.
3. Run command `Refresh Templates` if you changed ISY customizations.
4. Run command `Send Selected Message`.
5. External notification integrations can send command `GV10` payloads directly to ntfy.

## Notes

- The message dropdown is generated from ISY customization templates at startup/refresh.
- If `ISY_REST_URL` is not set, template refresh is skipped and direct publish mode still works.
- On startup, the node server sends a version announcement message to ntfy.
- If your ISY requires credentials for REST, set environment variables `ISY_USERNAME` and `ISY_PASSWORD` in your PG3 runtime environment.
- Version format is `yyyy.m.###` in `server.json` fields `version` and `profile_version`.
- PG3 Store listing uses `version`; `profile_version` controls profile update behavior.
- Current version is `2026.7.012` and revision runs from `001` through `999`.

## PG3 Store Description

### Short description

Send ISY notification customization messages to ntfy.sh with a selectable message template and secure KEY-based publishing.

### Long description

ISY2NTFY is a PG3 node server that bridges ISY notification customization templates to ntfy.sh.

Key features:

- Uses a simple `KEY` parameter for ntfy bearer-token authentication.
- Lets you select a message template from ISY email/notification customizations.
- Sends the selected template to your configured ntfy topic using a node command.
- Supports refresh of template options when customizations change.

Typical use cases:

- Push ISY alerts to phones and desktops through the ntfy app.
- Reuse existing ISY customization messages without duplicating text.
- Keep notification routing simple with topic-based delivery.

Required configuration:

- `KEY`: ntfy bearer token.
- `NTFY_URL`: ntfy base URL (optional; defaults to `https://ntfy.sh`).
- `ISY_REST_URL`: optional, only needed for ISY template-fetch mode.

Authorization model:

- Access tokens like `tk_...` are sent as `Authorization: Bearer <token>`.
- Example target URL: `https://ntfy.sh/your_private_topic`.

### Store copy by common limits

Short (<= 80 chars):

Send selected ISY notification templates to ntfy.sh from PG3.

Short (<= 120 chars):

Bridge ISY notification templates to ntfy.sh. Select a saved message and send it instantly from PG3.

Long (<= 500 chars):

ISY2NTFY is a PG3 node server that sends ISY notification customization templates to ntfy.sh. Configure KEY and NTFY_URL once, choose a template from ISY custom messages, and send with a command. Refresh updates template options when ISY customizations change.

Long (<= 1000 chars):

ISY2NTFY connects your ISY notification customization messages to ntfy.sh so you can deliver alerts to phones and desktops through ntfy topics. The node server is built for PG3 and supports KEY-based ntfy authentication with simple setup. After configuration, select a template in the controller node and trigger Send Selected Message to publish that content to your configured URL. If your ISY custom messages change, use Refresh Templates to reload dropdown options. Required settings are KEY and NTFY_URL, with optional ISY_REST_URL for template-fetch mode.

Keywords/Tags:

isy, pg3, polyglot, notifications, ntfy, ntfy.sh, push, alerts, eisy, polisy, templates, home automation
