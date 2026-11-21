"""Configurações do projeto Caderneta Digital.

Valores que mudam conforme o ambiente (senhas, chave secreta, modo debug) são
lidos do arquivo ".env" na raiz do projeto. Veja ".env.example".
"""
import os
import warnings
from pathlib import Path

from django.contrib.messages import constants as mensagens
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


def env_lista(nome, padrao=""):
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


# --- Segurança -------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "chave-apenas-para-desenvolvimento")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

SESSION_COOKIE_AGE = 60 * 60 * 12  # 12 horas: uma jornada de trabalho
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --- Aplicações --------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "contas",
    "caderneta",
    "estoque",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Banco de dados (PostgreSQL no Docker) -----------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "caderneta"),
        "USER": os.environ.get("POSTGRES_USER", "caderneta"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "caderneta"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5433"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Autenticação ------------------------------------------------------------

LOGIN_URL = "contas:entrar"
LOGIN_REDIRECT_URL = "caderneta:painel"
LOGOUT_REDIRECT_URL = "contas:entrar"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Idioma e fuso horário ---------------------------------------------------

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# --- Arquivos estáticos (Bootstrap fica em static/vendor: funciona offline) ---

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# O WhiteNoise serve os arquivos estáticos mesmo com DEBUG=False,
# sem precisar rodar "collectstatic" no uso local.
WHITENOISE_USE_FINDERS = True
# Sem "collectstatic" a pasta STATIC_ROOT não existe; o aviso do WhiteNoise é esperado.
warnings.filterwarnings("ignore", message="No directory at")

# --- Mensagens (alertas do Bootstrap) ----------------------------------------

MESSAGE_TAGS = {mensagens.ERROR: "danger"}
