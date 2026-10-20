"""``python manage.py seed`` -- fill the registry with some example models."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from agents import storage

MODELS = [
    # --- large language models -------------------------------------
    {
        "name": "GPT-4o",
        "slug": "gpt-4o",
        "handle": "openai",
        "kind": "llm",
        "description": "OpenAI's flagship multimodal model.",
        "config": {"context_window": 128_000, "streaming": True, "vision": True},
    },
    {
        "name": "Claude Sonnet",
        "slug": "claude-sonnet",
        "handle": "anthropic",
        "kind": "llm",
        "description": "Balanced model for long conversations.",
        "config": {"context_window": 200_000, "streaming": True},
    },
    {
        "name": "Llama 3.1 70B",
        "slug": "llama-3.1-70b",
        "handle": "meta",
        "kind": "llm",
        "description": "Open weights, runs yourself.",
        "config": {"context_window": 131_072, "self_hosted": True},
    },
    # --- text to speech --------------------------------------------
    {
        "name": "Alloy",
        "slug": "alloy",
        "handle": "openai",
        "kind": "tts",
        "description": "A neutral, balanced voice.",
        "config": {"voices": ["alloy", "echo", "fable", "onyx"], "formats": ["pcm", "opus", "mp3"]},
    },
    {
        "name": "Nova",
        "slug": "nova",
        "handle": "openai",
        "kind": "tts",
        "description": "A warm voice for friendly assistants.",
        "config": {"voices": ["nova"], "formats": ["pcm", "opus", "mp3"]},
    },
    # --- speech to text ---------------------------------------------
    {
        "name": "Whisper",
        "slug": "whisper",
        "handle": "openai",
        "kind": "stt",
        "description": "Multilingual transcription.",
        "config": {"languages": 99, "streaming": True, "punctuation": True},
    },
    {
        "name": "Deepgram Nova",
        "slug": "deepgram-nova",
        "handle": "deepgram",
        "kind": "stt",
        "description": "Low-latency streaming transcription.",
        "config": {"streaming": True, "interim_results": True, "smart_format": True},
    },
]

AGENTS = [
    {
        "name": "Customer Support",
        "slug": "customer-support",
        "handle": "acme-support",
        "prompt": (
            "You are a friendly support agent for Acme. Answer briefly, "
            "stay calm, and offer to pass the issue to a human when the "
            "question is about billing."
        ),
        "stt": "whisper",
        "model": "gpt-4o",
        "tts": "nova",
        "config": {
            "language": "en",
            "max_turns": 20,
            "greeting": "Hi, how can I help?",
            "interruptions": True,
        },
    },
    {
        "name": "Voice Note Scribe",
        "slug": "voice-note-scribe",
        "handle": "acme-scribe",
        "prompt": (
            "You transcribe voice notes. Keep the speaker's own words and "
            "do not add commentary."
        ),
        "stt": "deepgram-nova",
        "model": "claude-sonnet",
        "tts": "alloy",
        "config": {"language": "en", "summarise": False},
    },
]


class Command(BaseCommand):
    help = "Fill the model registry and the agent collection with examples."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="remove everything first",
        )

    def handle(self, *args, **options):
        database = storage.get_database()

        if options["reset"]:
            for name in ("models", "agent"):
                if name in database.list_collection_names():
                    database[name].delete_many({})
            self.stdout.write("cleared the collections")

        created_models = 0
        for payload in MODELS:
            try:
                storage.create_model(payload)
                created_models += 1
            except storage.Conflict:
                pass

        created_agents = 0
        for payload in AGENTS:
            try:
                storage.create_agent(payload)
                created_agents += 1
            except storage.Conflict:
                pass

        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {created_models} model(s) and {created_agents} agent(s)"
            )
        )
