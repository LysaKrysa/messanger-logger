# Messenger Message Logger — cookie-only Railway package

Modified from https://github.com/MS-Jahan/Messenger-Message-Logger
Includes the author's fbchat v1 source: https://github.com/MS-Jahan/fbchat/tree/v1
The included fbchat LICENSE applies to that library. The logger repository did not supply a license in its root; upstream rights remain with its author.

## Authentication
Only FB_SESSION_JSON is accepted. No email, password, OTP, or password-login fallback is enabled. If cookies are missing, malformed, expired or rejected, the worker exits with status 1. Refresh the Railway variable and redeploy. A persisted session file is not a fallback when the variable is absent. Cookie input is never intentionally printed.

Accepted formats:
- A JSON object: {"c_user":"YOUR_ID","xs":"YOUR_VALUE", ...}
- A browser-export array: [{"name":"c_user","value":"YOUR_ID"},{"name":"xs","value":"YOUR_VALUE"}, ...]

Use real cookie values from your own signed-in browser. Developer Tools > Application/Storage > Cookies > Facebook shows them. Include the other Facebook cookies present in your session as well. cookies.example.json is only a format example. Do not upload real cookies to GitHub.

## Deploy on Railway
1. Extract this ZIP.
2. Create a GitHub repository and upload the CONTENTS of this folder, including fbchat. Dockerfile must be at the repository root. Do not upload the ZIP itself as the application.
3. In Railway choose New Project > Deploy from GitHub repo, and select your repository.
4. Open the service > Variables. Add FB_SESSION_JSON with your complete JSON object or array. Paste directly without extra surrounding quotes.
5. Set USE_TELEGRAM=false for the first test (this is also the default).
6. Attach a persistent volume to this service, mounted at /data.
7. Use one replica. Disable Serverless/sleeping. Leave HTTP healthcheck, cron schedule, and public networking unset. Leave Start Command empty; Dockerfile supplies it.
8. Deploy. The first deployment may fail until the variable and volume are set.
9. Watch deployment logs for "Program Started! Cookie session accepted." Send a fresh test message to the account and verify logging.

No email/password variables, database, web server, PORT, or public domain are required.
railway.json selects Dockerfile builds and limits failure restarts to three retries.

## Storage and cookie refresh
Runtime files:
- /data/session.json
- /data/message_logs/<conversation>/<day>_<month>_<year>

The cookie variable seeds session.json every startup. During a run, the library's refreshed session is saved on reconnect. On the next process start, the variable takes precedence again. Update FB_SESSION_JSON when it expires, then redeploy. Never mount the volume on /app (it would hide application code).
Use Railway's volume tools or a service shell to inspect/download logs. Keep backups; message logs can grow indefinitely. Runtime message timestamps default to the container timezone; set TZ if desired.

## Optional Telegram
Create a bot with @BotFather, send /start to it, and obtain the destination chat ID. Set:
USE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat ID>
Then redeploy. The worker sends a startup message and forwards logged events. In groups, add your bot and use the group chat ID.

## Local launch
Use Python 3.11. Install requirements.txt, set FB_SESSION_JSON, and optionally DATA_DIR to a writable local folder, then run python railway_start.py. For Railway leave DATA_DIR unset (/data).

## Limitations and troubleshooting
This is an archived unofficial Messenger integration. Hosting successfully does not guarantee current Facebook login, encrypted-chat access, or event delivery. No live Facebook account was used to test this package. It only captures events it actually receives while running; it does not recover messages deleted before capture.

- Missing/invalid JSON: correct FB_SESSION_JSON; both c_user and xs must be non-empty strings.
- Session rejected: refresh cookies and resolve any checkpoint in your own browser. Password fallback is intentionally disabled.
- GraphQL, fb_dtsg or MQTT failures: upstream Facebook compatibility may be broken; changing Railway variables cannot repair the protocol.
- Telegram failures: disable Telegram first to isolate Messenger; verify token and chat ID.
- Logs disappear: attach the volume at /data before testing.

Only use your own account and conversations you are authorized to archive. Cookies grant account access: keep them in Railway Variables and out of repositories or screenshots. The upstream logger also prints some received message/event data, so restrict access to Railway deployment logs.

## Validation
Syntax checks passed for every bundled Python file. Offline checks passed for object/array cookie parsing, malformed input rejection, the cookie-only constructor failure path, and the valid-session constructor path. Live Facebook connectivity and deployment are not verified.
