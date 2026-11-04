# Telegram setup and topic repair

[← README](../README.md#telegram-forwarding) · [Troubleshooting](TROUBLESHOOTING.md)

## Initial setup

1. Create/select a bot with **@BotFather**.
2. Open its chat and send `/start`.
3. Get the intended destination's numeric chat ID.
4. Edit the private `messenger.env` with the token, chat ID, and forwarding options.
5. Restart the service to reload environment changes.

For a private bot chat, enable **Threaded mode** for that same bot in BotFather.
For a supergroup, enable **Topics** and give the bot **Manage Topics** admin rights.
The logger also needs `TELEGRAM_TOPICS=true`.

The logger does not discover bot updates or print tokens on your behalf. Keep
token-bearing URLs and update payloads private; they can contain account data.

## Read-only verification

```bash
sudo ./check-telegram-topics
```

The check uses only `getMe`, `getChat`, and, for a forum group, `getChatMember`.
It verifies the configured bot ID, reports its public username, checks
`getMe.has_topics_enabled` for private topic support, and checks the destination
type/group permission.

| Output | Action |
| :--- | :--- |
| Logger forwarding disabled | Set `TELEGRAM_ENABLED=true`, then restart. |
| Logger conversation topics disabled | Set `TELEGRAM_TOPICS=true`, then restart. |
| Telegram bot Threaded mode disabled/not reported | Verify the displayed bot in BotFather and its Threaded-mode setting. |
| Destination is not private or forum supergroup | Correct the chat ID or enable group Topics. |
| Missing Manage Topics | Give the configured bot that administrator permission. |
| HTTP 401 | Check/refresh the configured bot token privately. |
| Ready | Restart the listener and trigger a fresh eligible forwarded event. |

A failed setup check has a 30-second cooldown. A restarted process checks again
immediately. The standalone diagnostic always performs a fresh check.

With `TELEGRAM_MODE=deleted`, send a new Messenger message and unsend it after
capture. Sending an ordinary message alone is not a deletion-forwarding test.
Setting changes do not replay earlier failed notices.

## Delivery behavior

Formatted notices include available names, conversation titles, timestamps,
original/latest captured text, and attachment descriptions. Saved media uploads
as documents. Oversized Telegram uploads remain local with a notice. Local
attachment download limits are separate from Telegram's upload limit.

Sends are claimed in a local ledger before the request. Ambiguous sends are not
automatically retried, avoiding duplicates. Queue overflow and failed downloads
do not stop normal local capture.

Topic mappings are scoped by bot ID, destination chat ID, and Messenger
conversation ID. Changing the destination creates a new map. Same-name
conversations get distinct topic titles. Topic setup failure does not fall back
to mixing content into the destination's main chat.

## Unconfirmed topic creation

A network timeout can occur after Telegram creates a topic but before the
response reaches the logger. The map records this as unconfirmed and does not
blindly create another topic.

List local mappings, using only the numeric bot ID (the token prefix before
the colon), never the full token:

```bash
sudo ./manage-topics list --bot-id BOT_ID --chat-id CHAT_ID
```

Use `show-messages --include-ids` locally for the Messenger conversation ID.
Obtain the existing Telegram topic's numeric `message_thread_id` from your own
Telegram client/API context, then bind that existing topic:

```bash
sudo ./manage-topics bind \
  --bot-id BOT_ID --chat-id CHAT_ID \
  --conversation MESSENGER_CONVERSATION_ID \
  --topic-id EXISTING_TOPIC_ID --name "Conversation title"
```

For a negative group ID, use `--chat-id=-1001234567890` if your shell/argument
parser interprets a separate negative value as an option.

The repair utility writes only the local mapping; it makes no Telegram request.
It does not verify that a manually supplied topic ID belongs to the intended
destination, so check those IDs before binding.

Telegram copies are intentionally outside local retention. The logger does
not delete Telegram messages, files, chats, or topics during cleanup.

## Official references

- [Bot forums and Threaded mode](https://core.telegram.org/api/forum)
- [Bot API User capability fields](https://core.telegram.org/bots/api#user)
- [createForumTopic](https://core.telegram.org/bots/api#createforumtopic)
