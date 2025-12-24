import os
import subprocess
import sys
import datetime

# --- CONFIGURAÇÃO GERAL ---
# O pipeline.py roda na raiz de 'calc-precatorio-tjsp'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Pastas dos projetos vizinhos (Irmãos)
CRAWLER_DIR = os.path.abspath(os.path.join(BASE_DIR, "../crawler_tjsp"))
OCR_DIR = os.path.abspath(os.path.join(BASE_DIR, "../ocr-oficios-tjsp"))

# Diretório de Logs (dentro do projeto atual)
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log_message(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")

def run_step(step_name, work_dir, script_name, venv_path=None):
    """
    Executa um passo do pipeline em um subprocesso.
    """
    log_message(f"=== INICIANDO {step_name} ===")
    
    # Define qual Python usar (do venv ou do sistema)
    if venv_path:
        if os.name == 'nt': # Windows
            python_exe = os.path.join(venv_path, "Scripts", "python.exe")
        else: # Linux/Mac
            python_exe = os.path.join(venv_path, "bin", "python")
        
        if not os.path.exists(python_exe):
            log_message(f"[ERRO] Python Venv não encontrado: {python_exe}")
            return False
    else:
        python_exe = sys.executable

    script_path = os.path.join(work_dir, script_name)
    if not os.path.exists(script_path):
        log_message(f"[ERRO] Script não encontrado: {script_path}")
        return False

    # Comando
    cmd = [python_exe, "-X", "utf8", script_path]
    
    # Logs específicos para cada etapa
    log_file = os.path.join(LOG_DIR, f"{step_name.lower().replace(' ', '_')}.txt")

    # Variáveis de ambiente (Necessário para a Parte 3 funcionar dentro de src)
    env = os.environ.copy()
    env["PYTHONPATH"] = work_dir # Adiciona a raiz ao Path

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            # Roda o processo
            result = subprocess.run(
                cmd, 
                cwd=work_dir, # Muda o diretório de trabalho para a pasta do script
                stdout=f, 
                stderr=subprocess.STDOUT,
                env=env, # Passa o ambiente modificado
                text=True
            )
        
        if result.returncode == 0:
            log_message(f"=== {step_name} CONCLUÍDO (Sucesso) ===")
            return True
        else:
            log_message(f"=== {step_name} FALHOU (Código {result.returncode}) ===")
            print(f"Verifique o log: {log_file}")
            return False

    except Exception as e:
        log_message(f"[ERRO CRÍTICO] Falha ao executar {step_name}: {e}")
        return False

def main():
    log_message(">>> INICIANDO PIPELINE DE AUTOMAÇÃO <<<")

    # --- PARTE 1: CRAWLER ---
    # Assume que o venv do crawler está dentro da pasta dele
    if not run_step("1. Crawler TJSP", CRAWLER_DIR, "orchestrator_subprocess.py", os.path.join(CRAWLER_DIR, "env")):
        return

    # --- PARTE 2: OCR ---
    # Assume que o venv do OCR está dentro da pasta dele
    if not run_step("2. OCR TJSP", OCR_DIR, "run_sistema.py", os.path.join(OCR_DIR, "env")):
        return

    # --- PARTE 3: CÁLCULO (Este Projeto) ---
    # O script 'main.py' agora está dentro da pasta 'src'
    # Ajustamos o script_name para 'src/main.py'
    if not run_step("3. Calculo Precatorio", BASE_DIR, os.path.join("src", "main.py"), os.path.join(BASE_DIR, "env")):
        return

    log_message(">>> PIPELINE FINALIZADO COM SUCESSO <<<")

if __name__ == "__main__":
    main()