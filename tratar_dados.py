import csv
import requests
import re
import time
from difflib import SequenceMatcher

# FUNÇÕES DE VALIDAÇÃO LOCAIS
def valida_cpf_matematica(cpf):
    cpf = re.sub(r'\D', '', str(cpf))
    
    # CORREÇÃO PARA EXCEL: Recoloca o zero à esquerda se necessário
    if len(cpf) == 10:
        cpf = cpf.zfill(11)
        
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
        
    for i in range(9, 11):
        value = sum((int(cpf[num]) * ((i+1) - num) for num in range(0, i)))
        digit = ((value * 10) % 11) % 10
        if digit != int(cpf[i]):
            return False
    return True

def similaridade_nome(nome1, nome2):
    return SequenceMatcher(None, nome1.upper(), nome2.upper()).ratio()

def normalizar_registro(registro):
    # Remove pontuação, espaços e qualquer caractere que não seja letra/número
    return re.sub(r'[^A-Za-z0-9]', '', str(registro)).upper().strip()

def gerar_candidatos_registro(registro):
    """
    Gera formatos possíveis para tentar na API:
    - sem pontuação
    - com barra antes da última letra, quando fizer sentido
    """
    base = normalizar_registro(registro)
    candidatos = [base]

    m = re.fullmatch(r'(\d+)([A-Z])', base)
    if m:
        candidatos.insert(0, f"{m.group(1)}/{m.group(2)}")

    # Remove duplicados mantendo ordem
    vistos = set()
    ordenados = []
    for item in candidatos:
        if item not in vistos:
            vistos.add(item)
            ordenados.append(item)

    return ordenados

# FUNÇÃO DE INTEGRAÇÃO COM A API
def valida_cfn_api(categoria, regional, registro, nome, cpf):
    api_url = "https://cnn.cfn.org.br/application/front-resource/get-nutrir"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://proid.cfn.org.br/"
    }

    parametro_tecnico = "1" if str(categoria).strip().upper() == "TND" else "0"
    candidatos_registro = gerar_candidatos_registro(registro)

    for registro_limpo in candidatos_registro:
        data_passo1 = {
            "comando": "get-nutricionista",
            "options[crn]": regional,
            "options[registro]": registro_limpo,
            "options[nome]": "",
            "options[cpf]": "",
            "options[tecnico]": parametro_tecnico,
            "options[situacao]": "",
            "options[geral]": "true"
        }

        try:
            response = requests.post(api_url, headers=headers, data=data_passo1, timeout=10)
            result = response.json()

            if not result.get("success") or not result.get("data"):
                time.sleep(0.5)
                continue

            profissional_encontrado = None
            nomes_api_retornados = []

            for prof in result["data"]:
                nome_api = prof.get("nome", "").strip()
                nomes_api_retornados.append(nome_api)

                if (
                    nome.upper() in nome_api.upper()
                    or nome_api.upper() in nome.upper()
                    or similaridade_nome(nome, nome_api) >= 0.3
                ):
                    profissional_encontrado = prof
                    break

            if not profissional_encontrado:
                nomes_str = " | ".join(nomes_api_retornados)[:100]
                return False, f"Nome difere. Digitado: {nome} | API retornou: {nomes_str}..."

            time.sleep(0.5)

            data_passo2 = data_passo1.copy()
            data_passo2["options[cpf]"] = re.sub(r'\D', '', str(cpf))

            response_cpf = requests.post(api_url, headers=headers, data=data_passo2, timeout=10)
            result_cpf = response_cpf.json()

            if not result_cpf.get("success") or not result_cpf.get("data"):
                return False, "CPF informado não corresponde ao titular deste CRN/Registro."

            return True, "Validado com sucesso"

        except Exception as e:
            return False, f"Erro de conexão com API: {str(e)}"

    return False, "Nenhum(a) profissional encontrado(a) com esse CRN e Registro."

# ROTINA PRINCIPAL (LENDO E GRAVANDO)
def processar_csv(arquivo_entrada, arquivo_saida):
    print("Iniciando o processamento do CSV...")
    
    with open(arquivo_entrada, mode='r', encoding='utf-8-sig') as file_in, \
         open(arquivo_saida, mode='w', encoding='utf-8-sig', newline='') as file_out:
         
        reader = csv.DictReader(file_in, delimiter=';')
        
        # Cria o cabeçalho novo
        fieldnames = reader.fieldnames + ['Status_Validacao', 'Motivo_Erro']
        writer = csv.DictWriter(file_out, fieldnames=fieldnames, delimiter=';')
        writer.writeheader()
        
        total_linhas = 0
        
        # Set (memória) para guardar os CPFs que já passaram pelo script
        cpfs_processados = set()
        
        for row in reader:
            total_linhas += 1
            print(f"Processando linha {total_linhas}...", end=" ")
            
            categoria = row.get('Categoria', '').strip()
            regional = row.get('Regional', '').strip()
            registro = row.get('Registro', '').strip()
            nome = row.get('Nome', '').strip()
            cpf = row.get('CPF', '').strip()

            # Verificação de Duplicatas
            # Limpa o CPF apenas para a checagem na memória (ignora pontos/traços)
            cpf_limpo = re.sub(r'\D', '', cpf)
            if len(cpf_limpo) == 10:
                cpf_limpo = cpf_limpo.zfill(11)

            # Se o CPF já estiver na memória, marca como duplicata e pula
            if cpf_limpo and cpf_limpo in cpfs_processados:
                row['Status_Validacao'] = 'Inválido'
                row['Motivo_Erro'] = 'Duplicata'
                print(row['Status_Validacao'])
                writer.writerow(row)
                continue
            
            # Adiciona o CPF novo na memória para as próximas iterações
            if cpf_limpo:
                cpfs_processados.add(cpf_limpo)
            # =====================================================================

            # CPF é válido matematicamente?
            if not valida_cpf_matematica(cpf):
                row['Status_Validacao'] = 'Inválido'
                row['Motivo_Erro'] = 'CPF matematicamente inválido'
            else:
                # É profissional do conselho?
                if categoria in ['Nutricionista', 'TND']:
                    if not regional or not registro:
                        row['Status_Validacao'] = 'Inválido'
                        row['Motivo_Erro'] = 'Falta Regional ou Registro'
                    else:
                        valido, motivo = valida_cfn_api(categoria, regional, registro, nome, cpf)
                        row['Status_Validacao'] = 'Válido' if valido else 'Inválido'
                        row['Motivo_Erro'] = motivo
                        
                        # Pausa de segurança entre validações (Cloudflare)
                        time.sleep(1)
                else:
                    # Se for Sociedade Civil e o CPF estiver OK, passa direto
                    row['Status_Validacao'] = 'Válido'
                    row['Motivo_Erro'] = 'Validado (Sociedade Civil)'
            
            print(row['Status_Validacao'])
            writer.writerow(row)

    print(f"\nProcessamento concluído!")
    print(f"O resultado foi salvo em '{arquivo_saida}'.")

# EXECUÇÃO
if __name__ == "__main__":
    ARQUIVO_ENTRADA = "dados_brutos.csv"
    ARQUIVO_SAIDA = "dados_validados.csv"
    
    processar_csv(ARQUIVO_ENTRADA, ARQUIVO_SAIDA)