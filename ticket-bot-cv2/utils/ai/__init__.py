"""AI Control System shared by the dashboard and the Discord bot.

The dashboard is the management surface, the bot process is the trusted
executor for Discord-side work, and this package holds the code both need:
configuration, the provider client, knowledge, forms, routing, cases, the job
queue and the analysis engine. Everything is persisted in the bot's SQLite
database so both processes see the same state.

A Groq API key is read from the environment (``AI_API_KEY`` or
``GROQ_API_KEY``) and never stored in a table or sent to the browser.
"""

from __future__ import annotations

from utils.ai import engine, jobs, knowledge, provider, schema, settings, store

__all__ = ["engine", "jobs", "knowledge", "provider", "schema", "settings", "store"]
