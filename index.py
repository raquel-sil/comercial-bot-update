import os
import re
import random
from datetime import datetime, timezone, timedelta
import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Start RH - Automação Comercial")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURAÇÕES DA UNIPILE ---
UNIPILE_DSN = os.getenv("UNIPILE_DSN", "https://api45.unipile.com:17580").rstrip("/")
UNIPILE_API_KEY = os.getenv("UNIPILE_API_KEY", "")
ACCOUNT_ID = os.getenv("ACCOUNT_ID", "")

# --- REGRAS DE NEGÓCIO ---
LIMITE_DIARIO = 60
FUSO_BRASILIA = timezone(timedelta(hours=-3))

# Memória temporária de execução (substitui o banco de dados por enquanto)
historico_contatados = set()
contadores_dia = {"data": datetime.now(FUSO_BRASILIA).date().isoformat(), "total": 0}

TERMOS_BUSCA = [
    "Gerente de Recursos Humanos",
    "Head of People",
    "Talent Acquisition Manager",
    "Coordenador de RH",
    "BP RH",
    "People Partner"
]

CARGOS_PERMITIDOS = [
    r"\brh\b", r"recursos humanos", r"recrutamento", r"sele[çc][ãa]o",
    r"talent acquisition", r"business partner", r"talent partner",
    r"headhunter interno", r"people partner", r"people & culture",
    r"head of people", r"people lead"
]

EXCLUSAO_CONSULTORIA = [
    "consultoria", "consulting", "staffing", "outsourcing",
    "recruitment agency", "assessoria", "headhunting", "executive search"
]

MENSAGEM_TEMPLATE = (
    "Olá {primeiro_nome}, vi que você atua na área de pessoas da {empresa}. "
    "Acompanho o setor e gostaria de me conectar para trocarmos experiências sobre atração e gestão de talentos!"
)

# --- VALIDAÇÕES ---

def cargo_valido(cargo: str) -> bool:
    return any(re.search(padrao, (cargo or "").lower()) for padrao in CARGOS_PERMITIDOS)

def empresa_valida(empresa: str) -> bool:
    return not any(termo in (empresa or "").lower() for termo in EXCLUSAO_CONSULTORIA)

def total_enviados_hoje() -> int:
    hoje = datetime.now(FUSO_BRASILIA).date().isoformat()
    if contadores_dia["data"] != hoje:
        contadores_dia["data"] = hoje
        contadores_dia["total"] = 0
    return contadores_dia["total"]

def executar_um_ciclo():
    agora = datetime.now(FUSO_BRASILIA)
    if agora.weekday() > 4 or not (9 <= agora.hour < 18):
        return {"status": "fora_horario", "msg": f"Fora do horário comercial (09h-18h seg-sex). Horário atual: {agora.strftime('%H:%M')}."}

    if total_enviados_hoje() >= LIMITE_DIARIO:
        return {"status": "limite_atingido", "msg": f"Cota de {LIMITE_DIARIO} atingida hoje."}

    termo = random.choice(TERMOS_BUSCA)

    # 1. Busca perfis no LinkedIn
    url_search = f"{UNIPILE_DSN}/api/v1/linkedin/search"
    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "Content-Type": "application/json"
    }
    payload_search = {
        "api": "classic",
        "category": "people",
        "keywords": termo
    }

    try:
        res = requests.post(
            url_search,
            params={"account_id": ACCOUNT_ID, "limit": 10},
            json=payload_search,
            headers=headers,
            timeout=15
        )
        candidatos = res.json().get("items", []) if res.status_code == 200 else []
    except Exception as e:
        return {"status": "erro_busca", "msg": f"Falha na comunicação com Unipile: {str(e)}"}

    if not candidatos:
        return {"status": "sem_resultados", "msg": f"Nenhum perfil retornado para '{termo}' nesta rodada."}

    # 2. Filtra e dispara o convite
    for pessoa in candidatos:
        provider_id = pessoa.get("id")
        public_id = pessoa.get("public_identifier") or provider_id
        nome = pessoa.get("name") or "Profissional"
        cargo = pessoa.get("headline", "")
        empresa = pessoa.get("company", "sua empresa")

        if not provider_id:
            continue
        if provider_id in historico_contatados or public_id in historico_contatados:
            continue
        if not cargo_valido(cargo) or not empresa_valida(empresa):
            continue

        primeiro_nome = nome.split()[0].capitalize()
        mensagem = MENSAGEM_TEMPLATE.format(primeiro_nome=primeiro_nome, empresa=empresa)[:300]

        url_invite = f"{UNIPILE_DSN}/api/v1/users/invite"
        try:
            res_inv = requests.post(
                url_invite,
                json={"account_id": ACCOUNT_ID, "provider_id": provider_id, "message": mensagem},
                headers=headers,
                timeout=15
            )
            if res_inv.status_code in [200, 201]:
                historico_contatados.add(provider_id)
                historico_contatados.add(public_id)
                contadores_dia["total"] += 1
                return {"status": "sucesso", "msg": f"Convite enviado: {nome} ({cargo}) na empresa {empresa}."}
            else:
                return {"status": "erro_unipile", "msg": f"Código {res_inv.status_code}: {res_inv.text}"}
        except Exception as e:
            return {"status": "erro_envio", "msg": str(e)}

    return {"status": "vazio", "msg": "Perfis encontrados nesta rodada não passaram nos filtros de cargo/empresa."}

# --- ROTAS ---

@app.get("/")
def painel_html():
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Start RH - Automação LinkedIn</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
            .card { background: #1e293b; border: 1px solid #334155; padding: 36px; border-radius: 16px; max-width: 480px; width: 100%; text-align: center; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4); }
            h1 { color: #38bdf8; margin: 0 0 8px 0; font-size: 24px; }
            p { color: #94a3b8; font-size: 14px; margin-top: 0; margin-bottom: 24px; }
            .counter-box { background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 18px; margin-bottom: 24px; }
            .metric { font-size: 42px; font-weight: 700; color: #10b981; }
            .label { font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
            .btn { background: #0284c7; color: white; border: none; padding: 14px 20px; font-size: 15px; border-radius: 10px; cursor: pointer; transition: 0.2s ease; width: 100%; font-weight: 600; }
            .btn:hover { background: #0369a1; }
            .btn:disabled { background: #475569; cursor: not-allowed; }
            #log { margin-top: 20px; padding: 14px; background: #0f172a; border: 1px solid #334155; border-radius: 10px; font-size: 13px; text-align: left; min-height: 48px; color: #cbd5e1; line-height: 1.5; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Start RH - Bot</h1>
            <p>Operação Cloud Serverless</p>
            <div class="counter-box">
                <div class="label">Envios na Sessão de Hoje</div>
                <div class="metric" id="contador">--/60</div>
            </div>
            <button class="btn" id="btn-rodar" onclick="rodarCiclo()">Disparar 1 Convite Agora</button>
            <div id="log">Pronto para operar.</div>
        </div>

        <script>
            async function carregarMetricas() {
                try {
                    const res = await fetch('/api/stats');
                    const data = await res.json();
                    document.getElementById('contador').innerText = data.enviados_hoje + '/60';
                } catch(e) {}
            }

            async function rodarCiclo() {
                const btn = document.getElementById('btn-rodar');
                const log = document.getElementById('log');
                btn.disabled = true;
                btn.innerText = "Consultando LinkedIn...";
                log.innerText = "Buscando decisor de RH compatível e enviando convite...";

                try {
                    const res = await fetch('/api/run-cycle', { method: 'POST' });
                    const data = await res.json();
                    log.innerHTML = `<b>Status:</b> ${data.status}<br>${data.msg}`;
                    await carregarMetricas();
                } catch (e) {
                    log.innerText = "Falha ao se comunicar com a rota da API.";
                }

                btn.disabled = false;
                btn.innerText = "Disparar 1 Convite Agora";
            }

            window.onload = carregarMetricas;
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/stats")
def stats():
    return {"enviados_hoje": total_enviados_hoje()}

@app.post("/api/run-cycle")
def run_cycle():
    return executar_um_ciclo()
