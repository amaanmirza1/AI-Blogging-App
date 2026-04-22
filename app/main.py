from future import annotations

import math
from typing import Any

import jwt
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.params import Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.auth import create_access_token, decode_access_token, hash_password, verify_password
from app.config import settings
from app.db import execute, execute_many, fetch_all, fetch_one, init_db
from app.security import (
delete_uploaded_file,
ensure_csrf_token,
html_to_text,
sanitize_rich_text,
save_image,
validate_csrf,
)
from app.summarizer import Summarizer

def create_app() -> FastAPI:
application = FastAPI(title=settings.app_name)

# Session Middleware
application.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.session_https_only,
    max_age=60 * 60 * 12,
)

# Trusted Host Middleware
application.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]
)

# Static files
application.mount("/static", StaticFiles(directory=settings.base_dir / "static"), name="static")
application.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
return application


app = create_app()
templates = Jinja2Templates(directory=str(settings.base_dir / "templates"))
summarizer = Summarizer()
bearer_scheme = HTTPBearer(auto_error=False)


class RegisterPayload(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=6, max_length=120)


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class PostPayload(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    content: str = Field(min_length=20, max_length=50_000)


class CommentPayload(BaseModel):
    content: str = Field(min_length=2, max_length=800)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    is_admin: int
    created_at: str


class PostOut(BaseModel):
    id: int
    author_id: int
    title: str
    content: str
    featured_image: str | None
    summary: str | None
    created_at: str
    updated_at: str
    author_name: str
    like_count: int
    comment_count: int


class CommentOut(BaseModel):
    id: int
    post_id: int
    user_id: int
    content: str
    created_at: str
    author_name: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password_strength(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters long")
    if password.isalpha() or password.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Password should include both letters and numbers for better security",
        )


def validate_post_fields(title: str, content: str) -> tuple[str, str]:
    clean_title = title.strip()
    clean_content = sanitize_rich_text(content.strip())
    if len(clean_title) < 3:
        raise HTTPException(status_code=400, detail="Title must be at least 3 characters long")
    if len(html_to_text(clean_content)) < 20:
        raise HTTPException(status_code=400, detail="Post content must be at least 20 characters long")
    return clean_title, clean_content


def validate_comment_field(content: str) -> str:
    clean_content = html_to_text(content).strip()
    if len(clean_content) < 2:
        raise HTTPException(status_code=400, detail="Comment is too short")
    return clean_content[:800]


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.middleware("http")
async def load_current_user(request: Request, call_next):
    request.state.user = None
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; font-src 'self';"
    )
    return response


def excerpt_filter(value: str, limit: int = 180) -> str:
    text = html_to_text(value)
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


templates.env.filters["excerpt"] = excerpt_filter


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session["flash"] = {"message": message, "category": category}


def pop_flash(request: Request) -> dict[str, str] | None:
    return request.session.pop("flash", None)


def template_context(request: Request, **extra: Any) -> dict[str, Any]:
    current_user = get_current_user(request)
    return {
        "request": request,
        "current_user": current_user,
        "flash": pop_flash(request),
        "csrf_token": ensure_csrf_token(request),
        "app_name": settings.app_name,
        **extra,
    }


def get_current_user(request: Request):
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    if "session" not in request.scope:
        return None
    user_id = request.session.get("user_id")
    request.state.user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,)) if user_id else None
    return request.state.user


def require_user(request: Request):
    current_user = get_current_user(request)
    if current_user is None:
        flash(request, "Please login to continue.", "warning")
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return None


def require_admin(request: Request):
    redirect = require_user(request)
    if redirect:
        return redirect
    current_user = get_current_user(request)
    if not current_user["is_admin"]:
        flash(request, "Admin access required.", "error")
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return None


def paginate(total_items: int, page: int, page_size: int) -> dict[str, int]:
    total_pages = max(1, math.ceil(total_items / page_size)) if total_items else 1
    current_page = min(max(page, 1), total_pages)
    return {
        "page": current_page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
    }


def build_post_filters(search: str | None = None, author_id: int | None = None) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if author_id is not None:
        clauses.append("posts.author_id = ?")
        params.append(author_id)
    if search:
        clauses.append("(posts.title LIKE ? OR posts.content LIKE ?)")
        like_term = f"%{search.strip()}%"
        params.extend([like_term, like_term])
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_clause, tuple(params)


def count_posts(where_clause: str = "", params: tuple[Any, ...] = ()) -> int:
    query = f"""
        SELECT COUNT(*) AS total
        FROM (
            SELECT posts.id
            FROM posts
            JOIN users ON users.id = posts.author_id
            {where_clause}
            GROUP BY posts.id
        ) AS filtered_posts
    """
    result = fetch_one(query, params)
    return result["total"] if result else 0


def get_posts_with_stats(
    where_clause: str = "",
    params: tuple[Any, ...] = (),
    page: int = 1,
    page_size: int = 6,
):
    offset = (max(page, 1) - 1) * page_size
    query = f"""
        SELECT
            posts.*,
            users.name AS author_name,
            COUNT(DISTINCT likes.id) AS like_count,
            COUNT(DISTINCT comments.id) AS comment_count
        FROM posts
        JOIN users ON users.id = posts.author_id
        LEFT JOIN likes ON likes.post_id = posts.id
        LEFT JOIN comments ON comments.post_id = posts.id
        {where_clause}
        GROUP BY posts.id, users.name
        ORDER BY posts.created_at DESC
        LIMIT ? OFFSET ?
    """
    return fetch_all(query, params + (page_size, offset))


def create_user(name: str, email: str, password: str, is_admin: int = 0) -> int:
    clean_email = normalize_email(email)
    validate_password_strength(password)
    return execute(
        "INSERT INTO users (name, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        (name.strip(), clean_email, hash_password(password), is_admin),
    )


def get_user_by_email(email: str):
    return fetch_one("SELECT * FROM users WHERE email = ?", (normalize_email(email),))


def get_post_or_none(post_id: int):
    return fetch_one(
        """
        SELECT posts.*, users.name AS author_name
        FROM posts
        JOIN users ON users.id = posts.author_id
        WHERE posts.id = ?
        """,
        (post_id,),
    )


def get_post_with_stats(post_id: int):
    return fetch_one(
        """
        SELECT
            posts.*,
            users.name AS author_name,
            COUNT(DISTINCT likes.id) AS like_count,
            COUNT(DISTINCT comments.id) AS comment_count
        FROM posts
        JOIN users ON users.id = posts.author_id
        LEFT JOIN likes ON likes.post_id = posts.id
        LEFT JOIN comments ON comments.post_id = posts.id
        WHERE posts.id = ?
        GROUP BY posts.id, users.name
        """,
        (post_id,),
    )


def get_comments_for_post(post_id: int):
    return fetch_all(
        """
        SELECT comments.*, users.name AS author_name
        FROM comments
        JOIN users ON users.id = comments.user_id
        WHERE comments.post_id = ?
        ORDER BY comments.created_at DESC
        """,
        (post_id,),
    )


def build_post_response(post) -> dict[str, Any]:
    return {
        "id": post["id"],
        "author_id": post["author_id"],
        "title": post["title"],
        "content": post["content"],
        "featured_image": post["featured_image"],
        "summary": post["summary"],
        "created_at": post["created_at"],
        "updated_at": post["updated_at"],
        "author_name": post["author_name"],
        "like_count": post["like_count"],
        "comment_count": post["comment_count"],
    }


def build_comment_response(comment) -> dict[str, Any]:
    return {
        "id": comment["id"],
        "post_id": comment["post_id"],
        "user_id": comment["user_id"],
        "content": comment["content"],
        "created_at": comment["created_at"],
        "author_name": comment["author_name"],
    }


def get_api_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_api_post_or_404(post_id: int):
    post = get_post_with_stats(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def redirect_with_flash(request: Request, location: str, message: str, category: str) -> RedirectResponse:
    flash(request, message, category)
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return redirect_with_flash(request, request.headers.get("referer", "/"), str(exc.detail), "error")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/health")
def healthcheck():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = Query(default=""), page: int = Query(default=1, ge=1)):
    where_clause, params = build_post_filters(search=q or None)
    total_items = count_posts(where_clause, params)
    pagination = paginate(total_items, page, 6)
    posts = get_posts_with_stats(where_clause, params, pagination["page"], pagination["page_size"])
    return templates.TemplateResponse(
        "home.html",
        template_context(request, posts=posts, search_query=q, pagination=pagination),
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("register.html", template_context(request))


@app.post("/register")
def register(
    request: Request,
    csrf_token: str | None = Form(default=None),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    validate_csrf(request, csrf_token)
    existing = get_user_by_email(email)
    if existing:
        return redirect_with_flash(request, "/register", "Email already registered.", "error")

    is_first_user = 0 if fetch_one("SELECT id FROM users LIMIT 1") else 1
    user_id = create_user(name, email, password, is_admin=is_first_user)
    request.session["user_id"] = user_id
    return redirect_with_flash(request, "/dashboard", "Account created successfully.", "success")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", template_context(request))


@app.post("/login")
def login(request: Request, csrf_token: str | None = Form(default=None), email: str = Form(...), password: str = Form(...)):
    validate_csrf(request, csrf_token)
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return redirect_with_flash(request, "/login", "Invalid email or password.", "error")
    request.session["user_id"] = user["id"]
    return redirect_with_flash(request, "/dashboard", f"Welcome back, {user['name']}!", "success")


@app.post("/logout")
def logout(request: Request, csrf_token: str | None = Form(default=None)):
    validate_csrf(request, csrf_token)
    request.session.clear()
    return redirect_with_flash(request, "/", "You have been logged out.", "info")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, q: str = Query(default=""), page: int = Query(default=1, ge=1)):
    redirect = require_user(request)
    if redirect:
        return redirect
    current_user = get_current_user(request)
    where_clause, params = build_post_filters(search=q or None, author_id=current_user["id"])
    total_items = count_posts(where_clause, params)
    pagination = paginate(total_items, page, 6)
    posts = get_posts_with_stats(where_clause, params, pagination["page"], pagination["page_size"])
    return templates.TemplateResponse(
        "dashboard.html",
        template_context(request, posts=posts, search_query=q, pagination=pagination),
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    redirect = require_admin(request)
    if redirect:
        return redirect

    stats = {
        "users": fetch_one("SELECT COUNT(*) AS total FROM users")["total"],
        "posts": fetch_one("SELECT COUNT(*) AS total FROM posts")["total"],
        "comments": fetch_one("SELECT COUNT(*) AS total FROM comments")["total"],
        "likes": fetch_one("SELECT COUNT(*) AS total FROM likes")["total"],
    }
    users = fetch_all(
        """
        SELECT
            users.*,
            COUNT(DISTINCT posts.id) AS post_count,
            COUNT(DISTINCT comments.id) AS comment_count
        FROM users
        LEFT JOIN posts ON posts.author_id = users.id
        LEFT JOIN comments ON comments.user_id = users.id
        GROUP BY users.id
        ORDER BY users.created_at DESC
        """
    )
    recent_posts = get_posts_with_stats(page_size=8)
    return templates.TemplateResponse(
        "admin.html",
        template_context(request, stats=stats, users=users, posts=recent_posts),
    )


@app.post("/admin/users/{user_id}/toggle")
def toggle_admin(request: Request, user_id: int, csrf_token: str | None = Form(default=None)):
    validate_csrf(request, csrf_token)
    redirect = require_admin(request)
    if redirect:
        return redirect
    current_user = get_current_user(request)
    if current_user["id"] == user_id:
        return redirect_with_flash(request, "/admin", "You cannot change your own admin role here.", "warning")
    user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        return redirect_with_flash(request, "/admin", "User not found.", "error")
    next_value = 0 if user["is_admin"] else 1
    execute_many("UPDATE users SET is_admin = ? WHERE id = ?", (next_value, user_id))
    return redirect_with_flash(request, "/admin", "User role updated.", "success")


@app.get("/posts/new", response_class=HTMLResponse)
def new_post_page(request: Request):
    redirect = require_user(request)
    if redirect:
        return redirect
    return templates.TemplateResponse("post_form.html", template_context(request, post=None, action="/posts/new"))


@app.post("/posts/new")
async def create_post(
    request: Request,
    csrf_token: str | None = Form(default=None),
    title: str = Form(...),
    content: str = Form(...),
    featured_image: UploadFile | None = File(default=None),
):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect

    clean_title, clean_content = validate_post_fields(title, content)
    image_path = await save_image(featured_image)
    execute(
        "INSERT INTO posts (author_id, title, content, featured_image) VALUES (?, ?, ?, ?)",
        (get_current_user(request)["id"], clean_title, clean_content, image_path),
    )
    return redirect_with_flash(request, "/dashboard", "Post created successfully.", "success")


@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(request: Request, post_id: int):
    post = get_post_with_stats(post_id)
    if not post:
        return redirect_with_flash(request, "/", "Post not found.", "error")
    comments = get_comments_for_post(post_id)
    current_user = get_current_user(request)
    viewer_like = None
    if current_user:
        viewer_like = fetch_one(
            "SELECT id FROM likes WHERE post_id = ? AND user_id = ?",
            (post_id, current_user["id"]),
        )
    can_edit = current_user and (
        current_user["id"] == post["author_id"] or current_user["is_admin"]
    )
    return templates.TemplateResponse(
        "post_detail.html",
        template_context(
            request,
            post=post,
            comments=comments,
            can_edit=can_edit,
            viewer_has_liked=bool(viewer_like),
        ),
    )


@app.get("/posts/{post_id}/edit", response_class=HTMLResponse)
def edit_post_page(request: Request, post_id: int):
    redirect = require_user(request)
    if redirect:
        return redirect

    post = get_post_or_none(post_id)
    current_user = get_current_user(request)
    if not post or (post["author_id"] != current_user["id"] and not current_user["is_admin"]):
        return redirect_with_flash(request, "/dashboard", "You can only edit your own posts.", "error")
    return templates.TemplateResponse(
        "post_form.html",
        template_context(request, post=post, action=f"/posts/{post_id}/edit"),
    )


@app.post("/posts/{post_id}/edit")
async def edit_post(
    request: Request,
    post_id: int,
    csrf_token: str | None = Form(default=None),
    title: str = Form(...),
    content: str = Form(...),
    featured_image: UploadFile | None = File(default=None),
):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect

    post = get_post_or_none(post_id)
    current_user = get_current_user(request)
    if not post or (post["author_id"] != current_user["id"] and not current_user["is_admin"]):
        return redirect_with_flash(request, "/dashboard", "You can only edit your own posts.", "error")

    clean_title, clean_content = validate_post_fields(title, content)
    image_path = post["featured_image"]
    if featured_image and featured_image.filename:
        new_image = await save_image(featured_image)
        delete_uploaded_file(image_path)
        image_path = new_image
    execute_many(
        """
        UPDATE posts
        SET title = ?, content = ?, featured_image = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (clean_title, clean_content, image_path, post_id),
    )
    return redirect_with_flash(request, f"/posts/{post_id}", "Post updated successfully.", "success")


@app.post("/posts/{post_id}/delete")
def delete_post(request: Request, post_id: int, csrf_token: str | None = Form(default=None)):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect

    post = get_post_or_none(post_id)
    current_user = get_current_user(request)
    if not post or (post["author_id"] != current_user["id"] and not current_user["is_admin"]):
        return redirect_with_flash(request, "/dashboard", "You can only delete your own posts.", "error")

    delete_uploaded_file(post["featured_image"])
    execute_many("DELETE FROM comments WHERE post_id = ?", (post_id,))
    execute_many("DELETE FROM likes WHERE post_id = ?", (post_id,))
    execute_many("DELETE FROM posts WHERE id = ?", (post_id,))
    return redirect_with_flash(request, "/dashboard", "Post deleted.", "info")


@app.post("/posts/{post_id}/summarize")
async def summarize_post(request: Request, post_id: int, csrf_token: str | None = Form(default=None)):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect

    post = get_post_or_none(post_id)
    if not post:
        return redirect_with_flash(request, "/dashboard", "Post not found.", "error")

    summary = await summarizer.summarize(post["title"], html_to_text(post["content"]))
    execute_many("UPDATE posts SET summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (summary, post_id))
    return redirect_with_flash(request, f"/posts/{post_id}", "Summary generated.", "success")


@app.post("/posts/{post_id}/comments")
def add_comment(request: Request, post_id: int, csrf_token: str | None = Form(default=None), content: str = Form(...)):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect
    if not get_post_or_none(post_id):
        return redirect_with_flash(request, "/", "Post not found.", "error")
    execute(
        "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
        (post_id, get_current_user(request)["id"], validate_comment_field(content)),
    )
    return redirect_with_flash(request, f"/posts/{post_id}", "Comment added.", "success")


@app.post("/posts/{post_id}/like")
def toggle_like(request: Request, post_id: int, csrf_token: str | None = Form(default=None)):
    validate_csrf(request, csrf_token)
    redirect = require_user(request)
    if redirect:
        return redirect
    if not get_post_or_none(post_id):
        return redirect_with_flash(request, "/", "Post not found.", "error")
    current_user = get_current_user(request)
    existing = fetch_one(
        "SELECT id FROM likes WHERE post_id = ? AND user_id = ?",
        (post_id, current_user["id"]),
    )
    if existing:
        execute_many("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, current_user["id"]))
        message = "Like removed."
        category = "info"
    else:
        execute("INSERT INTO likes (post_id, user_id) VALUES (?, ?)", (post_id, current_user["id"]))
        message = "Post liked."
        category = "success"
    return redirect_with_flash(request, f"/posts/{post_id}", message, category)


@app.post("/api/v1/auth/register")
def api_register(payload: RegisterPayload):
    if get_user_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    is_first_user = 0 if fetch_one("SELECT id FROM users LIMIT 1") else 1
    user_id = create_user(payload.name, payload.email, payload.password, is_admin=is_first_user)
    user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    token = create_access_token(user_id, settings.access_token_expire_minutes)
    return {"access_token": token, "token_type": "bearer", "user": UserOut.model_validate(dict(user))}


@app.post("/api/v1/auth/login")
def api_login(payload: LoginPayload):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], settings.access_token_expire_minutes)
    return {"access_token": token, "token_type": "bearer", "user": UserOut.model_validate(dict(user))}


@app.get("/api/v1/auth/me")
def api_me(user=Depends(get_api_user)):
    return UserOut.model_validate(dict(user))


@app.get("/api/v1/posts")
def api_list_posts(
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    where_clause, params = build_post_filters(search=q or None)
    total_items = count_posts(where_clause, params)
    pagination = paginate(total_items, page, page_size)
    posts = [
        PostOut(**build_post_response(post))
        for post in get_posts_with_stats(where_clause, params, pagination["page"], pagination["page_size"])
    ]
    return {"items": posts, "pagination": pagination}


@app.post("/api/v1/posts")
def api_create_post(payload: PostPayload, user=Depends(get_api_user)):
    clean_title, clean_content = validate_post_fields(payload.title, payload.content)
    post_id = execute(
        "INSERT INTO posts (author_id, title, content) VALUES (?, ?, ?)",
        (user["id"], clean_title, clean_content),
    )
    return PostOut(**build_post_response(get_post_with_stats(post_id)))


@app.get("/api/v1/posts/{post_id}")
def api_get_post(post_id: int):
    post = get_api_post_or_404(post_id)
    comments = [CommentOut(**build_comment_response(comment)) for comment in get_comments_for_post(post_id)]
    return {"post": PostOut(**build_post_response(post)), "comments": comments}


@app.put("/api/v1/posts/{post_id}")
def api_update_post(post_id: int, payload: PostPayload, user=Depends(get_api_user)):
    post = get_api_post_or_404(post_id)
    if post["author_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed to edit this post")
    clean_title, clean_content = validate_post_fields(payload.title, payload.content)
    execute_many(
        "UPDATE posts SET title = ?, content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (clean_title, clean_content, post_id),
    )
    return PostOut(**build_post_response(get_post_with_stats(post_id)))


@app.delete("/api/v1/posts/{post_id}")
def api_delete_post(post_id: int, user=Depends(get_api_user)):
    post = get_api_post_or_404(post_id)
    if post["author_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed to delete this post")
    delete_uploaded_file(post["featured_image"])
    execute_many("DELETE FROM comments WHERE post_id = ?", (post_id,))
    execute_many("DELETE FROM likes WHERE post_id = ?", (post_id,))
    execute_many("DELETE FROM posts WHERE id = ?", (post_id,))
    return {"message": "Post deleted"}


@app.post("/api/v1/posts/{post_id}/summarize")
async def api_summarize_post(post_id: int, user=Depends(get_api_user)):
    post = get_api_post_or_404(post_id)
    if post["author_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Not allowed to summarize this post")
    summary = await summarizer.summarize(post["title"], html_to_text(post["content"]))
    execute_many("UPDATE posts SET summary = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (summary, post_id))
    return {"summary": summary}


@app.post("/api/v1/posts/{post_id}/comments")
def api_add_comment(post_id: int, payload: CommentPayload, user=Depends(get_api_user)):
    get_api_post_or_404(post_id)
    comment_id = execute(
        "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
        (post_id, user["id"], validate_comment_field(payload.content)),
    )
    comment = fetch_one(
        """
        SELECT comments.*, users.name AS author_name
        FROM comments
        JOIN users ON users.id = comments.user_id
        WHERE comments.id = ?
        """,
        (comment_id,),
    )
    return CommentOut(**build_comment_response(comment))


@app.post("/api/v1/posts/{post_id}/likes")
def api_toggle_like(post_id: int, user=Depends(get_api_user)):
    get_api_post_or_404(post_id)
    existing = fetch_one(
        "SELECT id FROM likes WHERE post_id = ? AND user_id = ?",
        (post_id, user["id"]),
    )
    if existing:
        execute_many("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, user["id"]))
        liked = False
    else:
        execute("INSERT INTO likes (post_id, user_id) VALUES (?, ?)", (post_id, user["id"]))
        liked = True
    post = get_post_with_stats(post_id)
    return {"liked": liked, "like_count": post["like_count"]}


@app.get("/api/v1/admin/overview")
def api_admin_overview(user=Depends(get_api_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    users = [UserOut.model_validate(dict(row)) for row in fetch_all("SELECT * FROM users ORDER BY created_at DESC")]
    posts = [PostOut(**build_post_response(post)) for post in get_posts_with_stats(page_size=100)]
    return {
        "stats": {
            "users": len(users),
            "posts": len(posts),
            "comments": fetch_one("SELECT COUNT(*) AS total FROM comments")["total"],
            "likes": fetch_one("SELECT COUNT(*) AS total FROM likes")["total"],
        },
        "users": users,
        "posts": posts,
    }
