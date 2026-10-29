import os
import re
import random
from datetime import datetime, timezone, timedelta
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

load_dotenv()

app = FastAPI(title="Start RH - Automação Comercial")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURAÇÕES ---
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "startrh-raquel-lab")
db = firestore.Client(project=PROJECT_ID)
COLLECTION_NAME = "contatos_outreach"

SIMULAR = os.getenv("SIMULAR_UNIPILE", "false").lower() == "true"
UNIPILE_DSN = os.getenv("UNIPILE_DSN", "https://api45.unipile.com:17580").rstrip("/")
UNIPILE_API_KEY = os.getenv("UNIPILE_API_KEY", "")
ACCOUNT_ID = os.getenv("ACCOUNT_ID", "")

LIMITE_DIARIO = 60
LIMITE_POR_EMPRESA = 3
FUSO_BRASILIA = timezone(timedelta(hours=-3))

TERMOS_BUSCA = ["Gerente de Recursos Humanos", "Head of People", "Talent Acquisition Manager", "Coordenador de RH", "BP RH", "People Partner"]
CARGOS_PERMITIDOS = [r"\brh\b", r"recursos humanos", r"recrutamento", r"sele[çc][ãa]o", r"talent acquisition", r"business partner", r"talent partner", r"headhunter interno", r"people partner", r"people & culture", r"head of people", r"people lead"]
EXCLUSAO_CONSULTORIA = ["consultoria", "consulting", "staffing", "outsourcing", "recruitment agency", "assessoria", "headhunting", "executive search"]
MENSAGEM_TEMPLATE = "Olá {primeiro_nome}, vi que você atua na área de pessoas da {empresa}. Acompanho o setor e gostaria de me conectar para trocarmos experiências sobre atração e gestão de talentos!"

# --- FUNÇÕES DE LÓGICA ---

def total_enviados_hoje() -> int:
    hoje = datetime.now(FUSO_BRASILIA).date().isoformat()
    col = db.collection(COLLECTION_NAME)
    filtro = FieldFilter("data_envio_dia", "==", hoje)
    return col.where(filter=filtro).count().get()[0][0].value

def cargo_valido(cargo: str) -> bool:
    return any(re.search(padrao, (cargo or "").lower()) for padrao in CARGOS_PERMITIDOS)

def empresa_valida(empresa: str) -> bool:
    return not any(termo in (empresa or "").lower() for termo in EXCLUSAO_CONSULTORIA)

def pode_contatar_empresa(empresa: str) -> bool:
    filtro = FieldFilter("empresa_lower", "==", empresa.strip().lower())
    return db.collection(COLLECTION_NAME).where(filter=filtro).count().get()[0][0].value < LIMITE_POR_EMPRESA

def perfil_ja_contatado(identificador: str) -> bool:
    return db.collection(COLLECTION_NAME).document(identificador).get().exists

def executar_um_ciclo():
    """Executa a busca e envia UM ÚNICO convite para encaixar no limite de tempo do Vercel."""
    agora = datetime.now(FUSO_BRASILIA)
    if agora.weekday() > 4 or not (9 <= agora.hour < 18):
        return {"status": "fora_horario", "msg": "Fora do horário comercial (09h-18h seg-sex)."}

    if total_enviados_hoje() >= LIMITE_DIARIO:
        return {"status": "limite_atingido", "msg": f"Cota de {LIMITE_DIARIO} atingida hoje."}

    termo = random.choice(TERMOS_BUSCA)
    
    # 1. Busca
    url_search = f"{UNIPILE_DSN}/api/v1/linkedin/search"
    headers = {"X-API-KEY": UNIPILE_API_KEY, "Content-Type": "application/json"}
    payload_search = {"api": "classic", "category": "people", "keywords": termo}
    
    try:
        res = requests.post(url_search, params={"account_id": ACCOUNT_ID, "limit": 10}, json=payload_search, headers=headers, timeout=10)
        candidatos = res.json().get("items", []) if res.status_code == 200 else []
    except:
        return {"status": "erro_busca", "msg": "Falha na comunicação com a Unipile."}

    if not candidatos:
        return {"status": "sem_resultados", "msg": f"Nenhum perfil para '{termo}' nesta rodada."}

    # 2. Filtra e Envia o primeiro válido
    for pessoa in candidatos:
        provider_id = pessoa.get("id")
        public_id = pessoa.get("public_identifier") or provider_id
        nome = pessoa.get("name") or "Profissional"
        cargo = pessoa.get("headline", "")
        empresa = pessoa.get("company", "sua empresa")

        if not provider_id or perfil_ja_contatado(public_id) or perfil_ja_contatado(provider_id): continue
        if not cargo_valido(cargo) or not empresa_valida(empresa): continue
        if empresa != "sua empresa" and not pode_contatar_empresa(empresa): continue

        primeiro_nome = nome.split()[0].capitalize()
        mensagem = MENSAGEM_TEMPLATE.format(primeiro_nome=primeiro_nome, empresa=empresa)[:300]

        # Envia convite
        url_invite = f"{UNIPILE_DSN}/api/v1/users/invite"
        try:
            res_inv = requests.post(url_invite, json={"account_id": ACCOUNT_ID, "provider_id": provider_id, "message": mensagem}, headers=headers, timeout=10)
            if res_inv.status_code in [200, 201]:
                # Salva no banco
                db.collection(COLLECTION_NAME).document(public_id).set({
                    "profile_id": public_id, "provider_id": provider_id,
                    "nome": nome, "cargo": cargo, "empresa": empresa, "empresa_lower": empresa.strip().lower(),
                    "mensagem": mensagem, "data_envio_dia": hoje
                })
                return {"status": "sucesso", "msg": f"Convite enviado: {nome} | {empresa}"}
        except Exception as e:
            return {"status": "erro_envio", "msg": str(e)}

    return {"status": "vazio", "msg": "Perfis encontrados não passaram no filtro de segurança."}

# --- ROTAS DA API ---

@app.get("/")
def painel_html():
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Start RH - Dashboard</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; color: #333; display: flex; flex-direction: column; align-items: center; padding: 40px 20px; }
            .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 500px; width: 100%; text-align: center; }
            h1 { color: #2c3e50; margin-bottom: 5px; }
            p { color: #7f8c8d; margin-bottom: 25px; }
            .metric { font-size: 48px; font-weight: bold; color: #27ae60; margin: 10px 0; }
            .btn { background-color: #3498db; color: white; border: none; padding: 15px 25px; font-size: 16px; border-radius: 8px; cursor: pointer; transition: 0.3s; width: 100%; font-weight: bold; }
            .btn:hover { background-color: #2980b9; }
            .btn:disabled { background-color: #bdc3c7; cursor: not-allowed; }
            #log { margin-top: 20px; padding: 15px; background: #ecf0f1; border-radius: 8px; font-size: 14px; text-align: left; min-height: 50px; color: #34495e;}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Start RH Bot</h1>
            <p>Operação 100% em Nuvem</p>
            <div>Enviados Hoje:</div>
            <div class="metric" id="contador">--/60</div>
            <button class="btn" id="btn-rodar" onclick="rodarCiclo()">Disparar 1 Convite Agora</button>
            <div id="log">Aguardando comando...</div>
        </div>

        <script>
            async function carregarMetricas() {
                const res = await fetch('/api/stats');
                const data = await res.json();
                document.getElementById('contador').innerText = data.enviados_hoje + '/60';
            }

            async function rodarCiclo() {
                const btn = document.getElementById('btn-rodar');
                const log = document.getElementById('log');
                btn.disabled = true;
                btn.innerText = "Processando...";
                log.innerText = "Buscando perfis no LinkedIn e validando filtros...";
                
                try {
                    const res = await fetch('/api/run-cycle', { method: 'POST' });
                    const data = await res.json();
                    log.innerHTML = `<b>Status:</b> ${data.status} <br><b>Info:</b> ${data.msg}`;
                    await carregarMetricas();
                } catch (e) {
                    log.innerText = "Erro ao conectar com o servidor.";
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