# VitalsSafe · Cartão de emergência de saúde

Protótipo de plataforma de saúde pessoal: a pessoa cadastra tipo sanguíneo, alergias, doenças crônicas, contato de emergência e documentos médicos, e ganha um **cartão de emergência com QR code**. Em uma emergência, quem escanear o QR vê só as informações vitais, numa página pública protegida por um token único.

## Funcionalidades

- Cadastro do paciente com tipo sanguíneo e contato de emergência
- Alergias e doenças crônicas
- Envio e revisão de documentos médicos
- **QR code** que leva à página de emergência (`/emergencia/{token}`), sem expor o CPF
- **Cartão digital** instalável no celular (PWA) e exportação para a **Apple Wallet** (`.pkpass`, quando os certificados estão configurados)

## Tecnologias

Python · FastAPI · SQLite · qrcode/Pillow · cryptography (assinatura PKCS#7 do `.pkpass`)

## Rodar localmente

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Acesse <http://localhost:8000>.

## Status e limitações

Projeto **pausado**, em fase de protótipo. Antes de qualquer uso com dados reais faltaria:

- **Autenticação de verdade**: hoje o acesso ao painel é só pelo CPF, sem senha nem sessão
- Controle de acesso por paciente nas rotas do painel e dos documentos
- Armazenamento seguro dos documentos enviados e adequação à LGPD (dados de saúde são dados sensíveis)
