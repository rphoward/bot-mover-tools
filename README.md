# bot-mover-tools

Helper scripts for the Bot Mover Grok Bot template.

A shared template carries only skill text, so the template's `bot-mover-tools`
skill fetches these 12 files from this repo, pinned to a specific commit, and
checks each one's sha256 before writing it into the matching skill folder.

Paths mirror the skill folders (`bot-mover-*/...`). Python 3, standard library only.

MIT licensed; see LICENSE.
