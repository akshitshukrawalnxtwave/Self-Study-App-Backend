# Self-Study-App-Backend

Django backend for the self-study teaching app: workspaces, chat, Claude Agent SDK integration, and lesson file serving.

## Documentation

| Doc | Description |
|-----|-------------|
| [**docs/PROJECT_GUIDE.md**](docs/PROJECT_GUIDE.md) | **Full project guide** — architecture, dev vs prod, storage, file layout, flows |
| [docs/API.md](docs/API.md) | HTTP API contract for the React frontend |
| [docs/Self Study Roadmap N(1).md](docs/Self%20Study%20Roadmap%20N(1).md) | Product milestones |

## Quick start

```bash
cp .env.example .env
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

See [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md) for environment variables, S3 setup, and production deployment.
