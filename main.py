import io
import uuid
import json
import zipfile
import hashlib
import sqlite3
from datetime import date
from enum import Enum
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, status, Request, Form, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
import qrcode
from PIL import Image, ImageDraw

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs7
    import cryptography
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

app = FastAPI(
    title="VitalsSafe - Plataforma de Saúde Pessoal",
    description="Sistema Pessoal de Emergência e Gestão de Documentos Médicos com Scanner Inteligente.",
    version="6.0.1"
)

DB_FILE = "vitals_safe.db"

# --- INICIALIZAÇÃO DO BANCO DE DADOS ---

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Pacientes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pacientes (
            cpf TEXT PRIMARY KEY,
            nome_completo TEXT NOT NULL,
            data_nascimento TEXT NOT NULL,
            tipo_sanguineo TEXT NOT NULL,
            contato_emergencia TEXT NOT NULL,
            token_emergencia TEXT NOT NULL UNIQUE
        )
    """)
    
    # Doenças Crônicas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS doencas_cronicas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_cpf TEXT NOT NULL,
            nome TEXT NOT NULL,
            status TEXT NOT NULL,
            observacoes TEXT,
            FOREIGN KEY (paciente_cpf) REFERENCES pacientes (cpf)
        )
    """)
    
    # Alergias
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alergias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_cpf TEXT NOT NULL,
            substancia TEXT NOT NULL,
            gravidade TEXT NOT NULL,
            FOREIGN KEY (paciente_cpf) REFERENCES pacientes (cpf)
        )
    """)
    
    # Documentos, Prontuários e Receitas com OCR/Fila de Revisão
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_cpf TEXT NOT NULL,
            nome_arquivo TEXT NOT NULL,
            tipo_documento TEXT NOT NULL,
            texto_extraido TEXT,
            status_revisao TEXT NOT NULL,
            data_upload TEXT NOT NULL,
            FOREIGN KEY (paciente_cpf) REFERENCES pacientes (cpf)
        )
    """)
    
    # Dados iniciais de teste se vazio
    cursor.execute("SELECT COUNT(*) FROM pacientes")
    if cursor.fetchone()[0] == 0:
        token_teste = "vitals-safe-9999"
        cursor.execute("""
            INSERT INTO pacientes (cpf, nome_completo, data_nascimento, tipo_sanguineo, contato_emergencia, token_emergencia)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("12345678901", "MARIA DA SILVA", "1990-08-25", "O+", "11988888888", token_teste))
        
        cursor.execute("INSERT INTO doencas_cronicas (paciente_cpf, nome, status, observacoes) VALUES (?, ?, ?, ?)",
                       ("12345678901", "Diabetes Tipo 1", "Ativo", "Uso de Insulina Diária"))
        cursor.execute("INSERT INTO alergias (paciente_cpf, substancia, gravidade) VALUES (?, ?, ?)",
                       ("12345678901", "Penicilina", "Crítica"))
        cursor.execute("""
            INSERT INTO documentos (paciente_cpf, nome_arquivo, tipo_documento, texto_extraido, status_revisao, data_upload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("12345678901", "receita_medica.jpg", "Receita", "Nova Alergia Identificada: Dipirona. Condição: Hipertensão Leve.", "Pendente", "2026-06-08"))
        
    conn.commit()
    conn.close()

init_db()

# --- ENUMS ---

class TipoSanguineo(str, Enum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"

# --- ENGINE AUXILIAR DE BANCO ---

def buscar_dados_completos(cpf: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT nome_completo, data_nascimento, tipo_sanguineo, contato_emergencia, token_emergencia FROM pacientes WHERE cpf = ?", (cpf,))
    p = cursor.fetchone()
    if not p:
        conn.close()
        return None
        
    cursor.execute("SELECT id, nome, status, observacoes FROM doencas_cronicas WHERE paciente_cpf = ?", (cpf,))
    doencas = [{"id": r[0], "nome": r[1], "status": r[2], "observacoes": r[3]} for r in cursor.fetchall()]
    
    cursor.execute("SELECT id, substancia, gravidade FROM alergias WHERE paciente_cpf = ?", (cpf,))
    alergias = [{"id": r[0], "substancia": r[1], "gravidade": r[2]} for r in cursor.fetchall()]
    
    cursor.execute("SELECT id, nome_arquivo, tipo_documento, texto_extraido, status_revisao, data_upload FROM documentos WHERE paciente_cpf = ?", (cpf,))
    docs = [{"id": r[0], "nome_arquivo": r[1], "tipo_documento": r[2], "texto_extraido": r[3], "status_revisao": r[4], "data_upload": r[5]} for r in cursor.fetchall()]
    
    conn.close()
    
    return {
        "cpf": cpf, 
        "nome_completo": p[0], 
        "data_nascimento": p[1], 
        "tipo_sanguineo": p[2],
        "contato_emergencia": p[3], 
        "token_emergencia": p[4], 
        "doencas": doencas, 
        "alergias": alergias, 
        "documentos": docs
    }

# --- TELA INICIAL (LOGIN E CADASTRO VISUAL) ---

@app.get("/", response_class=HTMLResponse)
def index_portal():
    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>VitalsSafe - Portal do Paciente</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0f19] text-white font-sans min-h-screen flex flex-col justify-between">
        <div class="container mx-auto px-4 py-12 max-w-5xl">
            <div class="text-center mb-12">
                <h1 class="text-4xl font-black tracking-wider text-blue-500">┼ VITALSSAFE</h1>
                <p class="text-gray-400 mt-2">Sua carteira de emergência e histórico médico inteligente auto-gerenciável</p>
            </div>
            
            <div class="grid md:grid-cols-2 gap-8 bg-[#111827] p-8 rounded-3xl border border-gray-800 shadow-2xl">
                <div class="border-b md:border-b-0 md:border-r border-gray-800 pb-8 md:pb-0 md:pr-8">
                    <h2 class="text-2xl font-bold mb-6 text-gray-200">Já possui cadastro?</h2>
                    <form action="/login" method="POST" class="space-y-4">
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">Digite seu CPF (Apenas números)</label>
                            <input type="text" name="cpf" required minlength="11" maxlength="11" placeholder="12345678901" class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-blue-500">
                        </div>
                        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-xl transition duration-200">Acessar Meu Painel Médico</button>
                    </form>
                </div>
                
                <div>
                    <h2 class="text-2xl font-bold mb-6 text-blue-400">Criar Nova Ficha</h2>
                    <form action="/cadastro" method="POST" class="space-y-4">
                        <div class="grid grid-cols-2 gap-4">
                            <div class="col-span-2">
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Nome Completo</label>
                                <input type="text" name="nome_completo" required class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-blue-500">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">CPF</label>
                                <input type="text" name="cpf" required minlength="11" maxlength="11" placeholder="Somente números" class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-blue-500">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Nascimento</label>
                                <input type="date" name="data_nascimento" required class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-blue-500">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Tipo Sanguíneo</label>
                                <select name="tipo_sanguineo" class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-blue-500">
                                    <option value="O+">O+</option>
                                    <option value="O-">O-</option>
                                    <option value="A+">A+</option>
                                    <option value="A-">A-</option>
                                    <option value="B+">B+</option>
                                    <option value="B-">B-</option>
                                    <option value="AB+">AB+</option>
                                    <option value="AB-">AB-</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">Contato Emergência</label>
                                <input type="text" name="contato_emergencia" placeholder="Ex: 11999999999" required class="w-full bg-[#1f2937] border border-gray-700 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-blue-500">
                            </div>
                        </div>
                        <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 px-4 rounded-xl transition duration-200 mt-2">Registrar e Gerar Cartão</button>
                    </form>
                </div>
            </div>
        </div>
        <footer class="text-center text-xs text-gray-600 py-6">VitalsSafe - Sistema de Prontuário Descentralizado Autônomo</footer>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/login")
def login_paciente(cpf: str = Form(...)):
    p = buscar_dados_completos(cpf)
    if not p:
        return HTMLResponse("<h2>Paciente não encontrado! <a href='/'>Voltar</a></h2>", status_code=404)
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)

@app.post("/cadastro")
def cadastrar_novo_paciente_form(nome_completo: str = Form(...), cpf: str = Form(...), data_nascimento: str = Form(...), tipo_sanguineo: str = Form(...), contato_emergencia: str = Form(...)):
    token_unico = str(uuid.uuid4())
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO pacientes (cpf, nome_completo, data_nascimento, tipo_sanguineo, contato_emergencia, token_emergencia)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (cpf, nome_completo.upper(), data_nascimento, tipo_sanguineo, contato_emergencia, token_unico))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return HTMLResponse("<h2>CPF já possui cadastro no VitalsSafe! <a href='/'>Voltar</a></h2>", status_code=400)
    conn.close()
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)


# --- INTERFACE CENTRAL: PAINEL EXCLUSIVO DO PACIENTE ---

@app.get("/pacientes/meu-perfil/{cpf}/painel", response_class=HTMLResponse)
def painel_gerenciamento_paciente(cpf: str):
    p = buscar_dados_completos(cpf)
    if not p:
        raise HTTPException(status_code=404, detail="Paciente inválido")
        
    alergias_linhas = ""
    for a in p["alergias"]:
        alergias_linhas += f"<li class='bg-[#1f2937] p-3 rounded-xl flex justify-between items-center border border-gray-700'><span>❌ <strong>{a['substancia']}</strong> ({a['gravidade']})</span></li>"
        
    doencas_linhas = ""
    for d in p["doencas"]:
        doencas_linhas += f"<li class='bg-[#1f2937] p-3 rounded-xl flex flex-col border border-gray-700'><span class='font-bold text-blue-400'>⚠️ {d['nome']}</span><span class='text-xs text-gray-400'>{d['status']} - {d['observacoes'] or ''}</span></li>"
        
    docs_linhas = ""
    for doc in p["documentos"]:
        badge_status = "<span class='bg-amber-500/20 text-amber-400 px-2 py-1 rounded text-xs font-bold'>Pendente Revisão</span>" if doc['status_revisao'] == 'Pendente' else "<span class='bg-emerald-500/20 text-emerald-400 px-2 py-1 rounded text-xs font-bold'>Arquivado e Sincronizado</span>"
        
        btn_revisar = ""
        if doc['status_revisao'] == 'Pendente':
            btn_revisar = f"""
            <form action="/pacientes/{cpf}/documentos/{doc['id']}/revisar" method="POST" class="mt-2 bg-[#111827] p-3 rounded-lg border border-amber-500/30">
                <p class="text-xs text-amber-300 mb-2 font-mono">🤖 Dados extraídos pela IA:</p>
                <textarea name="texto_final" class="w-full bg-[#1f2937] text-sm text-gray-200 rounded p-2 focus:outline-none mb-2 h-16">{doc['texto_extraido']}</textarea>
                <button type="submit" class="bg-amber-500 hover:bg-amber-600 text-black text-xs font-bold px-3 py-1.5 rounded transition">Homologar na minha Ficha</button>
            </form>
            """
            
        docs_linhas += f"""
        <div class="bg-[#1f2937] p-4 rounded-xl border border-gray-700 space-y-2">
            <div class="flex justify-between items-center">
                <span class="text-sm font-bold text-gray-300">📄 {doc['tipo_documento']}: {doc['nome_arquivo']}</span>
                {badge_status}
            </div>
            <p class="text-xs text-gray-400">Enviado em: {doc['data_upload']}</p>
            {btn_revisar}
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>VitalsSafe - Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0f19] text-white font-sans min-h-screen">
        <nav class="bg-[#111827] border-b border-gray-800 px-6 py-4 flex justify-between items-center">
            <span class="text-xl font-black text-blue-500 tracking-wider">┼ VITALSSAFE AREA</span>
            <a href="/" class="text-xs bg-gray-800 hover:bg-gray-700 px-3 py-1.5 rounded-lg text-gray-400 transition">Sair do Painel</a>
        </nav>
        
        <div class="container mx-auto px-4 py-8 max-w-6xl grid lg:grid-cols-3 gap-8">
            <div class="space-y-6">
                <div class="bg-gradient-to-br from-[#002d5a] to-[#004b8d] p-6 rounded-3xl border border-blue-400/20 shadow-xl">
                    <p class="text-xs uppercase tracking-wider text-blue-200 font-bold">Paciente Autenticado</p>
                    <h2 class="text-2xl font-black text-white mt-1 uppercase break-words">{p['nome_completo']}</h2>
                    <p class="text-sm text-blue-200 mt-1">CPF: {p['cpf'][:3]}.***.***-{p['cpf'][-2:]}</p>
                    
                    <div class="mt-6 pt-6 border-t border-white/20 flex justify-between items-center">
                        <div>
                            <p class="text-xs text-blue-200 uppercase font-medium">Sangue</p>
                            <p class="text-xl font-bold text-red-400">{p['tipo_sanguineo']}</p>
                        </div>
                        <div>
                            <p class="text-xs text-blue-200 uppercase font-medium">Nascimento</p>
                            <p class="text-sm font-bold">{p['data_nascimento']}</p>
                        </div>
                    </div>
                    
                    <div class="mt-6 space-y-2">
                        <a href="/pacientes/meu-perfil/{p['cpf']}/cartao-digital" target="_blank" class="block text-center bg-black hover:bg-neutral-900 text-white text-sm font-bold py-3 px-4 rounded-xl transition border border-white/10">📱 Abrir PWA / Tela de Início</a>
                        <a href="/pacientes/meu-perfil/{p['cpf']}/pkpass" class="block text-center bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold py-2.5 px-4 rounded-xl transition"> Obter arquivo Wallet (.pkpass)</a>
                    </div>
                </div>
                
                <div class="bg-[#111827] p-6 rounded-2xl border border-gray-800 space-y-4">
                    <h3 class="text-md font-bold border-b border-gray-800 pb-2 text-gray-300">➕ Inclusão Manual Rápida</h3>
                    <form action="/pacientes/{p['cpf']}/add-alergia" method="POST" class="space-y-2">
                        <p class="text-xs font-bold uppercase text-gray-400">Nova Alergia</p>
                        <div class="flex gap-2">
                            <input type="text" name="substancia" required placeholder="Ex: Dipirona" class="flex-1 bg-[#1f2937] border border-gray-700 rounded-lg px-3 py-1 text-sm text-white">
                            <input type="text" name="gravidade" required placeholder="Crítica" class="w-20 bg-[#1f2937] border border-gray-700 rounded-lg px-2 py-1 text-sm text-white">
                            <button type="submit" class="bg-blue-600 text-xs px-3 rounded-lg font-bold">Add</button>
                        </div>
                    </form>
                    <form action="/pacientes/{p['cpf']}/add-doenca" method="POST" class="space-y-2 pt-2 border-t border-gray-800">
                        <p class="text-xs font-bold uppercase text-gray-400">Nova Condição Crônica</p>
                        <div class="space-y-2">
                            <input type="text" name="nome" required placeholder="Ex: Hipertensão" class="w-full bg-[#1f2937] border border-gray-700 rounded-lg px-3 py-1 text-sm text-white">
                            <input type="text" name="observacoes" placeholder="Obs / Remédios" class="w-full bg-[#1f2937] border border-gray-700 rounded-lg px-3 py-1 text-sm text-white">
                            <button type="submit" class="w-full bg-blue-600 text-xs py-1.5 rounded-lg font-bold">Adicionar Condição</button>
                        </div>
                    </form>
                </div>
            </div>
            
            <div class="space-y-6">
                <div class="bg-[#111827] p-6 rounded-3xl border border-gray-800 shadow-xl">
                    <h3 class="text-lg font-bold text-red-400 mb-4 flex items-center gap-2">🛡️ Alergias Registradas</h3>
                    <ul class="space-y-2">
                        {alergias_linhas if alergias_linhas else "<p class='text-xs text-gray-500'>Nenhuma alergia adicionada.</p>"}
                    </ul>
                </div>
                
                <div class="bg-[#111827] p-6 rounded-3xl border border-gray-800 shadow-xl">
                    <h3 class="text-lg font-bold text-blue-400 mb-4 flex items-center gap-2">🩺 Histórico de Condições Crônicas</h3>
                    <ul class="space-y-2">
                        {doencas_linhas if doencas_linhas else "<p class='text-xs text-gray-500'>Nenhuma condição clínica mapeada.</p>"}
                    </ul>
                </div>
            </div>
            
            <div class="space-y-6">
                <div class="bg-[#111827] p-6 rounded-3xl border border-gray-800 shadow-xl space-y-4">
                    <h3 class="text-lg font-bold text-emerald-400 flex items-center gap-2">📷 Smart Scanner / Arquivador</h3>
                    <p class="text-xs text-gray-400">Suba fotos de receitas, exames ou prontuários.</p>
                    
                    <form action="/pacientes/{p['cpf']}/upload-documento" method="POST" enctype="multipart/form-data" class="border-2 border-dashed border-gray-700 rounded-2xl p-4 text-center hover:border-emerald-500/50 transition">
                        <input type="file" name="arquivo" required class="block w-full text-xs text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-xs file:font-semibold file:bg-emerald-600 file:text-white hover:file:bg-emerald-700 cursor-pointer">
                        <div class="mt-3">
                            <label class="inline-block text-xs font-bold uppercase text-gray-400 mr-2">Tipo:</label>
                            <select name="tipo_documento" class="bg-[#1f2937] border border-gray-700 rounded text-xs p-1 text-white">
                                <option value="Receita">Receita</option>
                                <option value="Prontuário">Prontuário</option>
                                <option value="Exame">Exame</option>
                            </select>
                        </div>
                        <button type="submit" class="mt-4 w-full bg-emerald-600 hover:bg-emerald-700 text-black font-bold py-2 text-xs rounded-xl transition">Escanear Documento</button>
                    </form>
                </div>
                
                <div class="bg-[#111827] p-6 rounded-3xl border border-gray-800 shadow-xl space-y-4">
                    <h3 class="text-md font-bold text-gray-300">📂 Documentos e Scanner Histórico</h3>
                    <div class="space-y-3 max-h-96 overflow-y-auto">
                        {docs_linhas if docs_linhas else "<p class='text-xs text-gray-500'>Nenhum documento digitalizado até o momento.</p>"}
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- ROTAS DE ATUALIZAÇÃO VIA FORMS ---

@app.post("/pacientes/{cpf}/add-alergia")
def painel_add_alergia(cpf: str, substancia: str = Form(...), gravidade: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO alergias (paciente_cpf, substancia, gravidade) VALUES (?, ?, ?)", (cpf, substancia, gravidade))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)

@app.post("/pacientes/{cpf}/add-doenca")
def painel_add_doenca(cpf: str, nome: str = Form(...), observacoes: Optional[str] = Form(None)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO doencas_cronicas (paciente_cpf, nome, status, observacoes) VALUES (?, ?, 'Ativo', ?)", (cpf, nome, observacoes))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)


# --- MOTOR DE SCANNER / OCR (SIMULADO) ---

@app.post("/pacientes/{cpf}/upload-documento")
async def processar_e_escanear_documento(cpf: str, tipo_documento: str = Form(...), arquivo: UploadFile = File(...)):
    nome = arquivo.filename.lower()
    
    if "receita" in nome or "alergia" in nome:
        texto_inteligente_ia = "Nova Alergia detectada: Dipirona Sódica. Gravidade: Alta. Recomendação de exclusão de anti-inflamatórios."
    elif "exame" in nome or "sangue" in nome:
        texto_inteligente_ia = "Nova Condição Crônica: Alteração Glicêmica de Jejum (Diabetes Controlada). Sincronizar medicação."
    else:
        texto_inteligente_ia = f"Documento genérico {tipo_documento} lido. Condição médica estável listada pelo médico responsável."
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO documentos (paciente_cpf, nome_arquivo, tipo_documento, texto_extraido, status_revisao, data_upload)
        VALUES (?, ?, ?, ?, 'Pendente', ?)
    """, (cpf, arquivo.filename, tipo_documento, texto_inteligente_ia, date.today().isoformat()))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)


@app.post("/pacientes/{cpf}/documentos/{doc_id}/revisar")
def homologar_revisao_documento_ia(cpf: str, doc_id: int, texto_final: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("UPDATE documentos SET texto_extraido = ?, status_revisao = 'Aprovado' WHERE id = ?", (texto_final, doc_id))
    
    texto_analise = texto_final.lower()
    if "alergia" in texto_analise:
        substancia_detectada = "Dipirona" if "dipirona" in texto_analise else "Nova Substância"
        cursor.execute("INSERT INTO alergias (paciente_cpf, substancia, gravidade) VALUES (?, ?, 'Alta (Validada por Scan)')", (cpf, substancia_detectada))
        
    if "condição" in texto_analise or "crônica" in texto_analise or "diabetes" in texto_analise or "hipertensão" in texto_analise:
        condicao_detectada = "Hipertensão" if "hipertensão" in texto_analise else "Diabetes" if "diabetes" in texto_analise else "Condição Geral"
        cursor.execute("INSERT INTO doencas_cronicas (paciente_cpf, nome, status, observacoes) VALUES (?, ?, 'Em tratamento', 'Identificado via Scanner de Documento')", (cpf, condicao_detectada))
        
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/pacientes/meu-perfil/{cpf}/painel", status_code=303)


# --- GERADOR INTERNO DO QR CODE ---

@app.get("/pacientes/meu-perfil/{cpf}/qrcode-cartao")
def obtener_qr_code_cartao(cpf: str, request: Request):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT token_emergencia FROM pacientes WHERE cpf = ?", (cpf,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Paciente não encontrado.")
    
    base_url = str(request.base_url).rstrip("/")
    url_publica_emergencia = f"{base_url}/emergencia/{row[0]}"
    
    qr = qrcode.QRCode(version=1, box_size=10, border=1)
    qr.add_data(url_publica_emergencia)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#002d5a", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


# --- INFRAESTRUTURA PWA ---

@app.get("/pacientes/meu-perfil/{cpf}/manifest.json")
def obtener_manifesto_pwa_dinamico(cpf: str, request: Request):
    base_url = str(request.base_url).rstrip("/")
    manifest_data = {
        "name": "VitalsSafe - Emergência",
        "short_name": "VitalsSafe",
        "start_url": f"{base_url}/pacientes/meu-perfil/{cpf}/cartao-digital",
        "display": "standalone",
        "background_color": "#002d5a",
        "theme_color": "#002d5a",
        "icons": [{"src": "/app-icon.png", "sizes": "192x192", "type": "image/png"}]
    }
    return JSONResponse(content=manifest_data)


@app.get("/app-icon.png")
def obter_icone_app():
    img = Image.new("RGBA", (192, 192), color="#002d5a")
    draw = ImageDraw.Draw(img)
    draw.rectangle([81, 40, 111, 152], fill="#ffffff")
    draw.rectangle([40, 81, 152, 111], fill="#ffffff")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


# --- INTERFACE DO APLICATIVO WEB PWA ---

@app.get("/pacientes/meu-perfil/{cpf}/cartao-digital", response_class=HTMLResponse)
def visualizar_cartao_digital_webview(cpf: str, request: Request):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT nome_completo, data_nascimento, tipo_sanguineo, token_emergencia FROM pacientes WHERE cpf = ?", (cpf,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Paciente não encontrado.")
        
    hoje = date.today()
    nasc = date.fromisoformat(row[1])
    idade = hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no, viewport-fit=cover">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-title" content="VitalsSafe">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <link rel="apple-touch-icon" href="/app-icon.png">
        <link rel="manifest" href="/pacientes/meu-perfil/{cpf}/manifest.json">
        <title>VitalsSafe Card</title>
        <style>
            body {{ background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; color: #fff; }}
            .app-container {{ width: 100%; max-width: 380px; padding: 20px; box-sizing: border-box; text-align: center; }}
            .card {{ background: linear-gradient(135deg, #002d5a 0%, #004b8d 100%); border-radius: 20px; box-shadow: 0 15px 35px rgba(0, 45, 90, 0.4); padding: 24px; text-align: left; border: 1px solid rgba(255, 255, 255, 0.1); }}
            .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.2); padding-bottom: 12px; margin-bottom: 20px; }}
            .logo-title {{ font-size: 16px; font-weight: 800; letter-spacing: 1px; }}
            .field {{ margin-bottom: 16px; }}
            .label {{ font-size: 10px; color: #a4c2e6; text-transform: uppercase; margin-bottom: 4px; }}
            .value {{ font-size: 16px; font-weight: 500; }}
            .row-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
            .qr-box {{ background-color: white; padding: 12px; border-radius: 14px; margin-top: 10px; display: flex; flex-direction: column; align-items: center; }}
            .qr-box img {{ width: 170px; height: 170px; }}
            .qr-caption {{ color: #002d5a; font-size: 9px; font-weight: 800; margin-top: 6px; }}
        </style>
    </head>
    <body>
        <div class="app-container">
            <div class="card">
                <div class="header">
                    <div class="logo-title">┼ VITALSSAFE</div>
                    <div style="font-size:11px; text-align:right;">EMERGÊNCIA</div>
                </div>
                <div class="field">
                    <div class="label">Cidadão</div>
                    <div style="font-size:19px; font-weight:700;">{row[0]}</div>
                </div>
                <div class="row-grid">
                    <div class="field">
                        <div class="label">CPF</div>
                        <div class="value">{cpf[:3]}.***.***-{cpf[-2:]}</div>
                    </div>
                    <div class="field">
                        <div class="label">Tipo Sanguíneo</div>
                        <div class="value" style="color:#ff6b6b; font-weight:bold;">{row[2]}</div>
                    </div>
                </div>
                <div class="row-grid">
                    <div class="field">
                        <div class="label">Nascimento</div>
                        <div class="value">{nasc.strftime("%d/%m/%Y")}</div>
                    </div>
                    <div class="field">
                        <div class="label">Idade</div>
                        <div class="value">{idade} anos</div>
                    </div>
                </div>
                <div class="qr-box">
                    <img src="/pacientes/meu-perfil/{cpf}/qrcode-cartao" alt="QR Code">
                    <div class="qr-caption">SCAN PARA FICHA DE SOCORRO</div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- GERADOR DO ARQUIVO .PKPASS ---

@app.get("/pacientes/meu-perfil/{cpf}/pkpass")
def baixar_carteira_apple_wallet(cpf: str, request: Request):
    p = buscar_dados_completos(cpf)
    if not p:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    base_url = str(request.base_url).rstrip("/")
    
    pass_json = {
        "formatVersion": 1,
        "passTypeIdentifier": "pass.com.vitalssafe.emergency",
        "serialNumber": f"VS-{p['token_emergencia'][:8].upper()}",
        "teamIdentifier": "12345ABCDE",
        "webServiceURL": f"{base_url}/api/wallet",
        "authenticationToken": str(uuid.uuid4()).replace("-", ""),
        "barcode": {
            "message": f"{base_url}/emergencia/{p['token_emergencia']}",
            "format": "PKBarcodeFormatQR",
            "messageEncoding": "iso-8859-1",
            "altText": "Scan para Ficha Médica Completa"
        },
        "organizationName": "VitalsSafe",
        "description": "Cartão de Emergência Médica",
        "foregroundColor": "rgb(255, 255, 255)",
        "backgroundColor": "rgb(0, 45, 90)",
        "labelColor": "rgb(164, 194, 230)",
        "generic": {
            "primaryFields": [{"key": "name", "label": "NOME", "value": p['nome_completo']}],
            "secondaryFields": [
                {"key": "blood", "label": "TIPO SANGUÍNEO", "value": p['tipo_sanguineo']},
                {"key": "birth", "label": "NASCIMENTO", "value": p['data_nascimento']}
            ],
            "backFields": [{"key": "info", "label": "Ajuda", "value": "Aproxime a câmera no QR Code frontal."}]
        }
    }
    
    icon_img = Image.new("RGBA", (29, 29), color="#002d5a")
    logo_img = Image.new("RGBA", (60, 60), color="#002d5a")
    icon_buffer, logo_buffer = io.BytesIO(), io.BytesIO()
    icon_img.save(icon_buffer, format="PNG")
    logo_img.save(logo_buffer, format="PNG")
    
    pass_bytes = json.dumps(pass_json, indent=4, ensure_ascii=False).encode('utf-8')
    manifest_data = {
        "pass.json": hashlib.sha1(pass_bytes).hexdigest(),
        "icon.png": hashlib.sha1(icon_buffer.getvalue()).hexdigest(),
        "logo.png": hashlib.sha1(logo_buffer.getvalue()).hexdigest(),
    }
    manifest_bytes = json.dumps(manifest_data).encode('utf-8')
    signature_bytes = b"MOCK_SIGNATURE_DATA"
    
    if HAS_CRYPTO:
        try:
            with open("pass.pem", "rb") as f:
                cert = cryptography.x509.load_pem_x509_certificate(f.read())
            with open("key.pem", "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            options = [pkcs7.PKCS7Options.DetachedSignature]
            signature_bytes = pkcs7.sign(manifest_bytes, cert, private_key, [], options)
        except Exception:
            pass
            
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr('pass.json', pass_bytes)
        zip_file.writestr('manifest.json', manifest_bytes)
        zip_file.writestr('signature', signature_bytes)
        zip_file.writestr('icon.png', icon_buffer.getvalue())
        zip_file.writestr('logo.png', logo_buffer.getvalue())
        
    zip_buffer.seek(0)
    headers = {"Content-Disposition": f"attachment; filename=vitalssafe_{cpf}.pkpass"}
    return StreamingResponse(zip_buffer, media_type="application/vnd.apple.pkpass", headers=headers)


# --- VISÃO PÚBLICA DO SOCORRISTA ---

@app.get("/emergencia/{token_emergencia}", response_class=HTMLResponse)
def consultar_dados_emergencia_publico(token_emergencia: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT cpf, nome_completo, tipo_sanguineo, contato_emergencia FROM pacientes WHERE token_emergencia = ?", (token_emergencia,))
    p_row = cursor.fetchone()
    
    if not p_row:
        conn.close()
        return "<html><body style='font-family:sans-serif; text-align:center; padding:50px; color:red;'><h2>⚠️ Registro Inválido.</h2></body></html>"
        
    cpf, nome_completo, tipo_sanguineo, contato_emergencia = p_row
    cursor.execute("SELECT nome, status, observacoes FROM doencas_cronicas WHERE paciente_cpf = ?", (cpf,))
    doencas_rows = cursor.fetchall()
    
    cursor.execute("SELECT substancia, gravidade FROM alergias WHERE paciente_cpf = ?", (cpf,))
    alergias_rows = cursor.fetchall()
    conn.close()
    
    partes = nome_completo.split()
    nome_msc = f"{partes[0]} {partes[-1][0]}." if len(partes) > 1 else partes[0]
    
    alergias_html = "".join([f"<li>❌ <strong>{r[0]}</strong> - ({r[1]})</li>" for r in alergias_rows]) or "<li>Nenhuma relatada</li>"
    doencas_html = "".join([f"<li>⚠️ <strong>{r[0]}</strong> ({r[2] or ''})</li>" for r in doencas_rows]) or "<li>Nenhuma relatada</li>"
    
    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Emergência</title></head>
    <body style="font-family:sans-serif; background-color:#f4f6f9; margin:0; padding:0;">
        <div style="background-color:#d32f2f; color:white; text-align:center; padding:20px;">
            <h2>Ficha Médica de Emergência</h2>
            <p>Paciente: {nome_msc}</p>
        </div>
        <div style="padding:15px; max-width:450px; margin:0 auto;">
            <div style="background:white; padding:15px; border-radius:10px; text-align:center; margin-bottom:15px;">
                <h3>Tipo Sanguíneo</h3>
                <span style="font-size:30px; font-weight:bold; color:#d32f2f;">{tipo_sanguineo}</span>
            </div>
            <div style="background:white; padding:15px; border-radius:10px; margin-bottom:15px;">
                <h3>Alergias</h3><ul>{alergias_html}</ul>
            </div>
            <div style="background:white; padding:15px; border-radius:10px; margin-bottom:15px;">
                <h3>Condições</h3><ul>{doencas_html}</ul>
            </div>
            <a href="tel:{contato_emergencia}" style="display:block; text-align:center; background:#2e7d32; color:white; padding:15px; border-radius:8px; font-weight:bold; text-decoration:none;">📞 LIGAR PARA EMERGÊNCIA</a>
        </div>
    </body>
    </html>
    """