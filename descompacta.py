import zipfile
from pathlib import Path

def extrair_zip_mesma_pasta(zip_path: Path):
    """Extrai o arquivo ZIP na mesma pasta onde está."""
    destino = zip_path.parent / zip_path.stem  # cria subpasta com o nome do arquivo (sem .zip)
    destino.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(destino)
    print(f"📂 Arquivos extraídos para: {destino.resolve()}")
    print("📦 Conteúdo extraído:")
    for nome in zf.namelist():
        print(f"   - {nome}")

# Exemplo de uso
if __name__ == "__main__":
    zip_path = Path("C:/Users/z3xai/OneDrive/Documentos/Projetos/Revisa/calc-precatorio-tjsp/ipca-e_SerieHist.zip")  # ajuste se necessário
    extrair_zip_mesma_pasta(zip_path)
