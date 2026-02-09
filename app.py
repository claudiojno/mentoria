import os
import json
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for
import pymysql
import pymysql.cursors
import boto3
import requests

app = Flask(__name__)

# ===== ALTERE AQUI PARA MOSTRAR DEPLOY ATUALIZADO =====
APP_VERSION = "v1.0.0"
DEPLOY_DATE = "2026-02-09"
# =======================================================

DEFAULT_QUERY = "SELECT id, name, email FROM customers ORDER BY id LIMIT 50;"
DEFAULT_ENV_SECRET_VAR = "RDS_SECRET_ARN"
S3_BUCKET_ENV = "S3_BUCKET"


# ==========================
# Helpers
# ==========================
def get_ecs_metadata():
    """
    Coleta metadados da task ECS (IP privado, task ARN, etc.)
    """
    metadata = {
        "private_ip": "N/A",
        "task_arn": "N/A",
        "cluster": "N/A",
        "availability_zone": "N/A"
    }
    
    try:
        # ECS Task Metadata Endpoint V4
        metadata_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
        if metadata_uri:
            # Pega info da task
            task_response = requests.get(f"{metadata_uri}/task", timeout=2)
            if task_response.status_code == 200:
                task_data = task_response.json()
                
                # IP privado
                containers = task_data.get("Containers", [])
                if containers:
                    networks = containers[0].get("Networks", [])
                    if networks:
                        metadata["private_ip"] = networks[0].get("IPv4Addresses", ["N/A"])[0]
                
                # Task ARN
                metadata["task_arn"] = task_data.get("TaskARN", "N/A")
                
                # Cluster
                metadata["cluster"] = task_data.get("Cluster", "N/A")
                
                # Availability Zone
                metadata["availability_zone"] = task_data.get("AvailabilityZone", "N/A")
    except Exception as e:
        print(f"Erro ao coletar metadados ECS: {e}")
    
    return metadata


def safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def validate_select_only(query: str) -> str | None:
    q = (query or "").strip()
    if not q:
        return "Query vazia."
    if not q.lower().startswith("select"):
        return "Apenas consultas SELECT são permitidas na demo."
    return None


def run_query_mysql(host, port, user, password, db, query):
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        ssl={"ssl": {}},  # demo (TLS). Se quiser remover, apague essa linha.
    )
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def get_secret_dict(secret_arn: str) -> dict:
    """
    Espera SecretString em JSON. Ex (RDS):
    { "username":"...", "password":"...", "engine":"mysql", "host":"...", "port":3306, "dbname":"demo" }
    """
    sm = boto3.client("secretsmanager")
    resp = sm.get_secret_value(SecretId=secret_arn)

    secret_string = resp.get("SecretString")
    if not secret_string:
        raise Exception("Secret não possui SecretString (ou está vazio).")

    try:
        return json.loads(secret_string)
    except json.JSONDecodeError:
        raise Exception("SecretString não é JSON válido.")


def extract_rds_params_from_secret(secret: dict, db_override: str = ""):
    host = secret.get("host") or secret.get("hostname")
    user = secret.get("username") or secret.get("user")
    password = secret.get("password")
    port = safe_int(secret.get("port", 3306), 3306)
    db = (
        db_override
        or secret.get("dbname")
        or secret.get("database")
        or secret.get("db")
    )

    if not host or not user or password is None or not db:
        raise Exception("Secret sem campos suficientes (host/username/password/dbname).")

    return host, port, user, password, db


def base_context(active: str):
    # Se você já tem base.html com logo embutido, não precisa passar nada aqui.
    ecs_metadata = get_ecs_metadata()
    return {
        "active": active,
        "brand": "UpperStack Cloud Lab",
        "app_version": APP_VERSION,
        "deploy_date": DEPLOY_DATE,
        "private_ip": ecs_metadata["private_ip"],
        "task_arn": ecs_metadata["task_arn"],
        "cluster": ecs_metadata["cluster"],
        "availability_zone": ecs_metadata["availability_zone"],
    }


# ==========================
# Routes
# ==========================
@app.get("/")
def root():
    return redirect(url_for("manual_page"))


# -------- RDS 1) Manual --------
@app.get("/manual")
def manual_page():
    ctx = base_context("manual")
    ctx.update({
        "result": None,
        "error": None,
        "form": {"port": "3306", "query": DEFAULT_QUERY}
    })
    return render_template("manual.html", **ctx)


@app.post("/manual/connect")
def manual_connect():
    form = {
        "host": request.form.get("host", "").strip(),
        "port": request.form.get("port", "3306").strip(),
        "user": request.form.get("user", "").strip(),
        "db": request.form.get("db", "").strip(),
        "query": request.form.get("query", DEFAULT_QUERY).strip(),
    }
    password = request.form.get("password", "")

    ctx = base_context("manual")
    ctx["form"] = form

    if not form["host"] or not form["user"] or not form["db"]:
        ctx["error"] = "Preencha host, user e database."
        ctx["result"] = None
        return render_template("manual.html", **ctx)

    port = safe_int(form["port"], 3306)
    err = validate_select_only(form["query"])
    if err:
        ctx["error"] = err
        ctx["result"] = None
        return render_template("manual.html", **ctx)

    try:
        rows = run_query_mysql(form["host"], port, form["user"], password, form["db"], form["query"])
        ctx["result"] = rows
        ctx["error"] = None
        return render_template("manual.html", **ctx)
    except Exception as e:
        ctx["error"] = f"Falha ao conectar/consultar: {e}"
        ctx["result"] = None
        return render_template("manual.html", **ctx)


# -------- RDS 2) Secret ARN digitado --------
@app.get("/secret")
def secret_page():
    ctx = base_context("secret")
    ctx.update({
        "result": None,
        "error": None,
        "form": {"query": DEFAULT_QUERY}
    })
    return render_template("secret.html", **ctx)


@app.post("/secret/connect")
def secret_connect():
    form = {
        "secret_arn": request.form.get("secret_arn", "").strip(),
        "db_override": request.form.get("db_override", "").strip(),
        "query": request.form.get("query", DEFAULT_QUERY).strip(),
    }

    ctx = base_context("secret")
    ctx["form"] = form

    if not form["secret_arn"]:
        ctx["error"] = "Informe o ARN do secret."
        ctx["result"] = None
        return render_template("secret.html", **ctx)

    err = validate_select_only(form["query"])
    if err:
        ctx["error"] = err
        ctx["result"] = None
        return render_template("secret.html", **ctx)

    try:
        secret = get_secret_dict(form["secret_arn"])
        host, port, user, password, db = extract_rds_params_from_secret(secret, form["db_override"])
        rows = run_query_mysql(host, port, user, password, db, form["query"])
        ctx["result"] = rows
        ctx["error"] = None
        return render_template("secret.html", **ctx)
    except Exception as e:
        ctx["error"] = f"Falha ao ler secret/conectar: {e}"
        ctx["result"] = None
        return render_template("secret.html", **ctx)


# -------- RDS 3) Env var -> Secret ARN (com botão) --------
@app.get("/env")
def env_page():
    ctx = base_context("env")
    ctx.update({
        "result": None,
        "error": None,
        "resolved_arn": None,
        "form": {"env_var": DEFAULT_ENV_SECRET_VAR, "db_override": "", "query": DEFAULT_QUERY}
    })
    return render_template("env.html", **ctx)


@app.post("/env/connect")
def env_connect():
    form = {
        "env_var": (request.form.get("env_var") or DEFAULT_ENV_SECRET_VAR).strip(),
        "db_override": (request.form.get("db_override") or "").strip(),
        "query": (request.form.get("query") or DEFAULT_QUERY).strip(),
    }

    ctx = base_context("env")
    ctx["form"] = form
    ctx["result"] = None
    ctx["error"] = None
    ctx["resolved_arn"] = None

    if not form["env_var"]:
        ctx["error"] = "Informe o nome da variável de ambiente (ex.: RDS_SECRET_ARN)."
        return render_template("env.html", **ctx)

    err = validate_select_only(form["query"])
    if err:
        ctx["error"] = err
        return render_template("env.html", **ctx)

    secret_arn = os.environ.get(form["env_var"], "").strip()
    ctx["resolved_arn"] = secret_arn

    if not secret_arn:
        ctx["error"] = f"A variável de ambiente '{form['env_var']}' não está definida no container."
        return render_template("env.html", **ctx)

    try:
        secret = get_secret_dict(secret_arn)
        host, port, user, password, db = extract_rds_params_from_secret(secret, form["db_override"])
        rows = run_query_mysql(host, port, user, password, db, form["query"])
        ctx["result"] = rows
        return render_template("env.html", **ctx)
    except Exception as e:
        ctx["error"] = f"Falha ao ler env/secret/conectar: {e}"
        return render_template("env.html", **ctx)


# -------- S3 Upload --------
@app.get("/s3")
def s3_page():
    ctx = base_context("s3")
    bucket = os.environ.get(S3_BUCKET_ENV, "").strip()
    ctx.update({
        "bucket": bucket,
        "result": None,
        "error": None,
        "form": {"key": "", "text": ""}
    })
    return render_template("s3.html", **ctx)


@app.post("/s3/upload")
def s3_upload():
    ctx = base_context("s3")
    bucket = os.environ.get(S3_BUCKET_ENV, "").strip()
    ctx["bucket"] = bucket

    if not bucket:
        ctx["error"] = f"Defina a env var {S3_BUCKET_ENV} com o nome do bucket."
        ctx["result"] = None
        ctx["form"] = {"key": "", "text": ""}
        return render_template("s3.html", **ctx)

    key = (request.form.get("key") or "").strip()
    text = (request.form.get("text") or "").strip()
    file = request.files.get("file")

    if file and file.filename:
        if not key:
            key = file.filename
        body = file.read()
        content_type = file.mimetype or "application/octet-stream"
    else:
        if not key:
            ctx["error"] = "Informe um Key (ex.: uploads/hello.txt) ou envie um arquivo."
            ctx["result"] = None
            ctx["form"] = {"key": key, "text": text}
            return render_template("s3.html", **ctx)

        if not text:
            ctx["error"] = "Você não enviou arquivo; então preencha o campo de texto para upload."
            ctx["result"] = None
            ctx["form"] = {"key": key, "text": text}
            return render_template("s3.html", **ctx)

        body = text.encode("utf-8")
        content_type = "text/plain; charset=utf-8"

    s3 = boto3.client("s3")
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata={
                "uploaded-by": "upperstack-ecs-lab",
                "uploaded-at": datetime.now(timezone.utc).isoformat()
            }
        )
        ctx["error"] = None
        ctx["result"] = {"bucket": bucket, "key": key, "uri": f"s3://{bucket}/{key}"}
        ctx["form"] = {"key": key, "text": text}
        return render_template("s3.html", **ctx)
    except Exception as e:
        ctx["error"] = f"Falha ao enviar para o S3: {e}"
        ctx["result"] = None
        ctx["form"] = {"key": key, "text": text}
        return render_template("s3.html", **ctx)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
