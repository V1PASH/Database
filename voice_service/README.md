# Voice agent service

A small Django microservice that keeps its data in **minidb**.

It stores two things:

- **`models`** -- a registry of the models an agent can be built from: an
  **LLM**, a **text-to-speech** model or a **speech-to-text** model
- **`agent`** -- the agents themselves: a name, a prompt, and the three
  models each one is wired together from

```
DATABASES  = {"default": {...django's own tables...}}
MINIDB_PATH = "voice.db"       # <- the models and the agents live here
```

## Running it

```bash
python manage.py seed           # some example models and agents
python manage.py runserver       # http://127.0.0.1:8000/
```

`MINIDB_PATH` decides where the data goes. Point it somewhere else and the
service uses a different database:

```bash
MINIDB_PATH=/var/lib/voice/voice.db python manage.py runserver
```

## The API

### Health

```bash
curl http://127.0.0.1:8000/api/health
```

```json
{
  "status": "ok",
  "minidb": "0.6.2",
  "database": "/srv/voice.db",
  "models": 8,
  "modelsByKind": {"llm": 4, "tts": 2, "stt": 2},
  "agents": 2
}
```

### Models

```bash
# every model
curl http://127.0.0.1:8000/api/models

# just the LLMs, or the TTS ones, or the STT ones
curl "http://127.0.0.1:8000/api/models?kind=llm"
curl "http://127.0.0.1:8000/api/models?kind=tts"
curl "http://127.0.0.1:8000/api/models?kind=stt"

# search by name, slug or handle
curl "http://127.0.0.1:8000/api/models?q=claude"

# one model
curl http://127.0.0.1:8000/api/models/gpt-4o

# create one
curl -X POST http://127.0.0.1:8000/api/models \
  -H 'Content-Type: application/json' \
  -d '{"name": "Mistral Large", "kind": "llm", "handle": "mistral",
       "config": {"context_window": 32000}}'

# change one
curl -X PATCH http://127.0.0.1:8000/api/models/gpt-4o \
  -H 'Content-Type: application/json' \
  -d '{"description": "OpenAI'"'"'s flagship model."}'

# remove one
curl -X DELETE http://127.0.0.1:8000/api/models/gpt-4o
```

A model looks like this:

```json
{
  "name": "GPT-4o",
  "slug": "gpt-4o",
  "handle": "openai",
  "kind": "llm",
  "description": "OpenAI's flagship multimodal model.",
  "config": {"context_window": 128000, "streaming": true, "vision": true},
  "active": true,
  "_id": {"$oid": "6ad784d61010b825753f799c"}
}
```

### Agents

```bash
curl http://127.0.0.1:8000/api/agents
curl http://127.0.0.1:8000/api/agents/customer-support

curl -X POST http://127.0.0.1:8000/api/agents \
  -H 'Content-Type: application/json' \
  -d '{"name": "Customer Support",
       "prompt": "You are a friendly support agent for Acme.",
       "stt": "whisper",
       "model": "gpt-4o",
       "tts": "nova",
       "config": {"language": "en", "max_turns": 20, "greeting": "Hi, how can I help?"}}'

curl -X PATCH http://127.0.0.1:8000/api/agents/customer-support \
  -H 'Content-Type: application/json' -d '{"config": {"greeting": "Hello there!"}}'

curl -X DELETE http://127.0.0.1:8000/api/agents/customer-support
```

An agent looks like this:

```json
{
  "name": "Customer Support",
  "slug": "customer-support",
  "handle": "acme-support",
  "prompt": "You are a friendly support agent for Acme.",
  "stt": "whisper",
  "model": "gpt-4o",
  "tts": "nova",
  "config": {"language": "en", "max_turns": 20, "greeting": "Hi, how can I help?"},
  "_id": {"$oid": "6ad784d61010b825753f79a3"}
}
```

## What the storage layer adds

A document store gives you none of these, so `agents/storage.py` puts them
back:

| | |
|---|---|
| **Schemas** | Every collection has a validator, so a document that does not fit is refused on the way in -- `400` with the offending field named |
| **Unique slugs** | A unique index on `slug` in both collections -- `409` on a duplicate |
| **References** | An agent may only name models that exist, and the right kind of model: `stt` must be an STT model, `model` an LLM one, `tts` a TTS one |
| **No broken links** | A model that an agent uses cannot be deleted -- `409` naming the agents that hold it |
| **Slugs** | Made from the name when you do not send one |
| **Merging** | A `PATCH` merges into `config` instead of replacing it |

## Tests

```bash
python manage.py test agents
```

## Layout

```
voice_service/
  manage.py
  voice_service/        the Django project (settings, urls, wsgi)
  agents/
    models.py           the two collections and their schemas
    storage.py          everything the API does to the database
    api.py              the JSON API
    urls.py
    management/commands/seed.py
    tests/test_api.py
```
