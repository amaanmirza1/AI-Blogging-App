# AI Blogging Platform

A polished AI-powered blogging platform starter with:

- user authentication
- blog post CRUD
- admin panel
- JWT REST API
- comments and likes
- search and pagination
- rich text editor
- image uploads
- SQLite persistence
- LLM-based post summarization with a local fallback
- Docker deployment

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The first registered user becomes the admin automatically.

## Features

- Register and login with secure password hashing
- CSRF protection for all web forms
- Security headers and trusted host checks
- Create, edit, delete, search, and paginate blog posts
- Rich text editor with sanitized HTML output
- Featured image uploads with file type and size validation
- Admin dashboard for users and content
- JWT-based API under `/api/v1`
- Post comments and likes
- Summarize posts using an OpenAI-compatible API
- Automatic fallback summary when no API key is configured

## Environment Variables

- `SECRET_KEY`: session signing key
- `OPENAI_API_KEY`: optional API key for LLM summarization
- `OPENAI_MODEL`: model name for summarization
- `OPENAI_BASE_URL`: compatible API base URL
- `APP_NAME`: app title shown in the UI
- `ACCESS_TOKEN_EXPIRE_MINUTES`: JWT token lifetime
- `DATABASE_URL`: database connection string like `sqlite:///blog.db`
- `SESSION_HTTPS_ONLY`: set `true` behind HTTPS in production
- `MAX_UPLOAD_SIZE_MB`: max image upload size
- `HOST`: host used for local startup
- `PORT`: port used for local startup
- `ALLOWED_HOSTS`: comma-separated hostnames allowed by the app

## API Highlights

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `GET /api/v1/posts?q=ai&page=1&page_size=10`
- `POST /api/v1/posts`
- `GET /api/v1/posts/{id}`
- `PUT /api/v1/posts/{id}`
- `DELETE /api/v1/posts/{id}`
- `POST /api/v1/posts/{id}/summarize`
- `POST /api/v1/posts/{id}/comments`
- `POST /api/v1/posts/{id}/likes`
- `GET /api/v1/admin/overview`

Use `Authorization: Bearer <token>` for protected API routes.

## Tests

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Notes

- Data is stored in `blog.db` in the project root by default.
- Uploaded images are stored in the local `uploads/` folder.
- This is strong for demos, portfolios, and college projects.
- For full production use, you would typically move from SQLite to PostgreSQL and add backup/monitoring layers.
