#!/usr/bin/env python3
"""Monitor STF / Banco Master.

Coleta novidades em três fontes públicas do STF relacionadas ao "Banco
Master" e notifica via Telegram + docs/timeline.md. Ver SOURCES.md para o
detalhamento de cada endpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
SEEDS_PATH = BASE_DIR / "seeds.json"
TIMELINE_PATH = BASE_DIR / "docs" / "timeline.md"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "MonitorSTFBancoMaster/1.0 (contato: walter.correa.neto@gmail.com)"
)
# Nota: portal.stf.jus.br bloqueia (403) User-Agents que não pareçam de
# navegador; por isso o UA acima combina uma string de navegador real com
# a identificação do bot, para não ser bloqueado pelo WAF e ainda assim
# ser identificável nos logs do STF.
TIMEOUT = 20
MAX_RETRIES = 3
BACKOFF_BASE = 2  # segundos: 2, 4, 8...

NOME_PARTE = "BANCO MASTER"

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
)


def request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.request(method, url, timeout=TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            if attempt < MAX_RETRIES:
                sleep_for = BACKOFF_BASE**attempt
                print(
                    f"  [retry {attempt}/{MAX_RETRIES}] {url} falhou ({exc}); "
                    f"aguardando {sleep_for}s",
                    file=sys.stderr,
                )
                time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_seeds() -> list[dict]:
    return json.loads(SEEDS_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Fonte A: processos em que BANCO MASTER é parte
# --------------------------------------------------------------------------
def fetch_fonte_a() -> list[dict]:
    url = "https://digital.stf.jus.br/integracoes-processos/api/public/partes/processos"
    itens: list[dict] = []

    for tramitacao in ("sim", "nao"):
        pagina = 1
        while True:
            params = {
                "nome": NOME_PARTE,
                "tipoPesquisa": "PARTE",
                "processosEmTramitacao": tramitacao,
                "processosPorPagina": 50,
                "pagina": pagina,
            }
            resp = request_with_retry("GET", url, params=params)
            data = resp.json()
            partes = data.get("partes", [])
            for parte in partes:
                content = "|".join(
                    [
                        "A",
                        str(parte.get("id")),
                        str(parte.get("processoId")),
                        str(parte.get("processoIdentificacao")),
                        str(parte.get("pessoaNome")),
                        str(parte.get("processoEmTramitacao")),
                    ]
                )
                link = (
                    "https://portal.stf.jus.br/processos/detalhe.asp?incidente="
                    f"{parte.get('processoId')}"
                )
                mensagem = (
                    f"🆕 <b>Novo processo com {NOME_PARTE} como parte</b>\n"
                    f"{parte.get('processoIdentificacao')} — {parte.get('pessoaNome')}\n"
                    f"Autuado em {parte.get('processoDataAutuacao')} · "
                    f"Tramitação: {parte.get('processoEmTramitacao')}\n"
                    f"{link}"
                )
                itens.append(
                    {
                        "hash": sha256(content),
                        "source": "fonte_a",
                        "mensagem": mensagem,
                        "timeline": (
                            f"- **[Fonte A]** {parte.get('processoIdentificacao')} — "
                            f"{parte.get('pessoaNome')} (autuado "
                            f"{parte.get('processoDataAutuacao')}, tramitação "
                            f"{parte.get('processoEmTramitacao')}) — [{link}]({link})"
                        ),
                    }
                )

            total_paginas = data.get("totalDePaginas", 1) or 1
            if pagina >= total_paginas:
                break
            pagina += 1

    return itens


# --------------------------------------------------------------------------
# Fonte B: andamentos por incidente (scrape de seeds.json)
# --------------------------------------------------------------------------
def fetch_fonte_b(seeds: list[dict]) -> list[dict]:
    itens: list[dict] = []
    base_processo_url = "https://portal.stf.jus.br/processos/"

    for seed in seeds:
        incidente = seed["incidente"]
        nome_seed = seed["nome"]
        url = f"{base_processo_url}abaAndamentos.asp"
        resp = request_with_retry(
            "GET", url, params={"incidente": incidente, "imprimir": ""}
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        detalhe_url = f"{base_processo_url}detalhe.asp?incidente={incidente}"

        for item in soup.select(".andamento-item"):
            data_el = item.select_one(".andamento-data")
            nome_el = item.select_one(".andamento-nome")
            extra_el = item.select_one(".col-md-9.p-0")

            data_txt = data_el.get_text(strip=True) if data_el else ""
            nome_txt = nome_el.get_text(strip=True) if nome_el else ""
            extra_txt = extra_el.get_text(strip=True) if extra_el else ""

            docs = []
            for a in item.select(".andamento-docs a[href]"):
                href = a["href"].strip()
                if href:
                    docs.append(
                        href
                        if href.startswith("http")
                        else base_processo_url + href
                    )

            if not data_txt and not nome_txt:
                continue

            content = "|".join(["B", str(incidente), data_txt, nome_txt, extra_txt, *docs])
            descricao = nome_txt + (f" — {extra_txt}" if extra_txt else "")
            doc_txt = docs[0] if docs else None

            mensagem = (
                f"🆕 <b>Novo andamento — {nome_seed}</b> (incidente {incidente})\n"
                f"[{data_txt}] {descricao}\n"
                + (f"Documento: {doc_txt}\n" if doc_txt else "")
                + detalhe_url
            )
            itens.append(
                {
                    "hash": sha256(content),
                    "source": "fonte_b",
                    "mensagem": mensagem,
                    "timeline": (
                        f"- **[Fonte B — {nome_seed}]** [{data_txt}] {descricao}"
                        + (f" ([doc]({doc_txt}))" if doc_txt else "")
                        + f" — [{detalhe_url}]({detalhe_url})"
                    ),
                }
            )

    return itens


# --------------------------------------------------------------------------
# Fonte C: notícias do STF
# --------------------------------------------------------------------------
def fetch_fonte_c() -> list[dict]:
    url = "https://noticias.stf.jus.br/wp-json/wp/v2/posts"
    resp = request_with_retry(
        "GET", url, params={"search": NOME_PARTE.lower(), "per_page": 20}
    )
    posts = resp.json()

    itens: list[dict] = []
    for post in posts:
        titulo = post.get("title", {}).get("rendered", "").strip()
        link = post.get("link", "")
        data_pub = post.get("date", "")
        modified = post.get("modified", "")

        content = "|".join(["C", str(post.get("id")), post.get("slug", ""), modified])
        mensagem = f"🆕 <b>Notícia STF</b>\n{titulo} ({data_pub[:10]})\n{link}"
        itens.append(
            {
                "hash": sha256(content),
                "source": "fonte_c",
                "mensagem": mensagem,
                "timeline": f"- **[Fonte C]** {titulo} ({data_pub[:10]}) — [{link}]({link})",
            }
        )

    return itens


# --------------------------------------------------------------------------
# Notificação e persistência
# --------------------------------------------------------------------------
def send_telegram(mensagem: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT")
    if not token or not chat_id:
        print("  [telegram] TELEGRAM_TOKEN/TELEGRAM_CHAT não configurados; pulando envio.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        request_with_retry(
            "POST",
            url,
            data={
                "chat_id": chat_id,
                "text": mensagem,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
    except requests.RequestException as exc:
        print(f"  [telegram] falha ao enviar notificação: {exc}", file=sys.stderr)


def append_timeline(novidades: list[dict]) -> None:
    if not novidades:
        return

    TIMELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    linhas = [f"\n## {ts}\n"]
    for item in novidades:
        linhas.append(item["timeline"])
    bloco = "\n".join(linhas) + "\n"

    if TIMELINE_PATH.exists():
        with TIMELINE_PATH.open("a", encoding="utf-8") as f:
            f.write(bloco)
    else:
        header = "# Timeline — Monitor STF / Banco Master\n"
        TIMELINE_PATH.write_text(header + bloco, encoding="utf-8")


def main() -> int:
    state = load_state()
    seeds = load_seeds()

    todos_itens: list[dict] = []

    fontes = {
        "Fonte A (partes/processos)": fetch_fonte_a,
        "Fonte B (andamentos)": lambda: fetch_fonte_b(seeds),
        "Fonte C (notícias)": fetch_fonte_c,
    }

    for nome_fonte, fn in fontes.items():
        print(f"Coletando {nome_fonte}...")
        try:
            itens = fn()
            print(f"  {len(itens)} item(ns) coletado(s).")
            todos_itens.extend(itens)
        except Exception as exc:  # noqa: BLE001
            print(f"  [erro] {nome_fonte} falhou: {exc}", file=sys.stderr)

    novidades = [item for item in todos_itens if item["hash"] not in state]
    print(f"\n{len(novidades)} novidade(s) encontrada(s) de {len(todos_itens)} item(ns) totais.")

    for item in novidades:
        print(f"  - [{item['source']}] {item['timeline']}")
        send_telegram(item["mensagem"])
        state[item["hash"]] = {
            "source": item["source"],
            "first_seen": datetime.now(timezone.utc).isoformat(),
        }

    append_timeline(novidades)
    save_state(state)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
